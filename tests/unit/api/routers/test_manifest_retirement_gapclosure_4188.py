"""Manifest retirement gap-closure — MoonLadderStudios/MoonMind#4188.

Second bounded remediation pass for the verifier gaps at 2e65e9e22
(AC-02..AC-07 PARTIAL). Every test below exercises a real production
boundary (router via TestClient, TemporalExecutionService, recurring
service, migration guard, repo source scan) rather than a test-only model.
Companion to tests/unit/api/routers/test_manifest_retirement_remediation_4188.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _temporal_db(tmp_path: Path):
    from api_service.db.models import Base
    from moonmind.config.settings import settings

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_gapclosure_4188.db"
    engine = create_async_engine(db_url, future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_backend
        settings.workflow.temporal_artifact_root = original_root


@asynccontextmanager
async def _recurring_db(tmp_path: Path):
    from api_service.db.models import Base

    db_path = tmp_path / "recurring_gapclosure_4188.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


def _mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:id")
    adapter.update_schedule = AsyncMock()
    adapter.pause_schedule = AsyncMock()
    adapter.unpause_schedule = AsyncMock()
    adapter.trigger_schedule = AsyncMock(
        return_value=SimpleNamespace(workflow_id=None, run_id=None, scheduled_at=None)
    )
    adapter.delete_schedule = AsyncMock()
    adapter.describe_schedule = AsyncMock()
    adapter.resolve_workflow_task_queue = MagicMock(return_value="mm.workflow.user.v2")
    return adapter


async def _insert_historical_manifest_record(session, *, state=None) -> object:
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalExecutionOwnerType,
        TemporalWorkflowType,
    )

    record = TemporalExecutionCanonicalRecord(
        workflow_id=f"mm:historical-manifest:{uuid4().hex[:8]}",
        run_id=uuid4().hex,
        namespace="default",
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        owner_id=str(uuid4()),
        owner_type=TemporalExecutionOwnerType.USER,
        state=state or MoonMindWorkflowState.COMPLETED,
        entry="manifest",
        manifest_ref="artifact://manifest/historical",
        parameters={"task": {"instructions": "historical manifest work"}},
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


def _override_user(app: FastAPI, *, is_superuser: bool = True) -> SimpleNamespace:
    from api_service.auth_providers import get_current_user, get_current_user_optional

    user = SimpleNamespace(
        id=uuid4(),
        email="gapclosure@example.com",
        is_active=True,
        is_superuser=is_superuser,
        roles=[],
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_optional] = lambda: user
    return user


def _override_temporal_client(app: FastAPI) -> None:
    from api_service.api.routers.executions import get_temporal_client

    def _get_empty_client() -> SimpleNamespace:
        return SimpleNamespace()

    app.dependency_overrides[get_temporal_client] = _get_empty_client


# ---------------------------------------------------------------------------
# AC-02: retired-source rerun / recovery / update paths reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_rerun_from_manifest_source_rejected_before_launch(
    tmp_path: Path,
) -> None:
    """_create_fresh_rerun_execution on a ManifestIngest row cannot launch."""
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    async with _temporal_db(tmp_path) as session:
        record = await _insert_historical_manifest_record(session)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        with pytest.raises(TemporalExecutionValidationError, match="was retired"):
            await service._create_fresh_rerun_execution(
                record,  # type: ignore[arg-type]
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                idempotency_key=None,
            )
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_update_request_rerun_from_manifest_source_rejected(
    tmp_path: Path,
) -> None:
    """update_execution RequestRerun on a terminal ManifestIngest row rejects."""
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    async with _temporal_db(tmp_path) as session:
        record = await _insert_historical_manifest_record(session)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        with pytest.raises(TemporalExecutionValidationError, match="was retired"):
            await service.update_execution(
                workflow_id=record.workflow_id,
                update_name="RequestRerun",
            )
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_typed_and_failed_step_recovery_reject_manifest_source(
    tmp_path: Path,
) -> None:
    """Typed/failed-step recovery never launches from a ManifestIngest source."""
    from moonmind.workflows.temporal.service import TemporalExecutionService
    from moonmind.workflows.temporal.service import TemporalExecutionValidationError
    from moonmind.workflows.temporal.service import (
        TemporalExecutionRecoveryCheckpointError,
    )

    async with _temporal_db(tmp_path) as session:
        record = await _insert_historical_manifest_record(session)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        # Typed recovery validates its admitted target first; either way the
        # retired source is rejected before any launch side effect.
        with pytest.raises(
            (TemporalExecutionValidationError, TemporalExecutionRecoveryCheckpointError)
        ):
            await service.create_typed_recovery_execution(
                record,  # type: ignore[arg-type]
                recovery_target={
                    "source": {
                        "workflowId": record.workflow_id,
                        "runId": record.run_id,
                    }
                },
            )
        # Failed-step recovery gates on UserWorkflow sources before state.
        with pytest.raises(TemporalExecutionValidationError, match="only available for"):
            await service.create_failed_step_recovery_execution(
                record,  # type: ignore[arg-type]
                recovery_checkpoint_ref=None,
                idempotency_key=f"recovery:{uuid4()}",
            )
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]


def test_draft_and_preset_shapes_with_retired_intent_reject_at_schema() -> None:
    """Saved-draft resubmission and preset-expanded payloads hit the schema guard."""
    from pydantic import ValidationError

    from moonmind.schemas.temporal_models import CreateExecutionRequest

    # Copied historical / saved-draft payload carrying the retired type.
    with pytest.raises(ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"task": {"instructions": "copied draft"}},
                "manifestArtifactRef": "artifact://manifest/draft",
            }
        )
    # Preset expansion never rewrites the workflow type: a retired intent
    # survives expansion verbatim and still rejects at the shared boundary.
    with pytest.raises(ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {
                    "task": {
                        "instructions": "preset work",
                        "presetSchedule": {"presetSlug": "demo", "goal": "x"},
                    }
                },
            }
        )


@pytest.mark.asyncio
async def test_integration_callback_does_not_create_retired_work(
    tmp_path: Path,
) -> None:
    """ingest_integration_callback signals the existing row; never launches."""
    from moonmind.workflows.temporal.service import TemporalExecutionService

    async with _temporal_db(tmp_path) as session:
        from api_service.db.models import MoonMindWorkflowState as _ExecutingState

        # Non-terminal so the callback reaches the signal path (terminal
        # rows return the stored record by design without signaling).
        record = await _insert_historical_manifest_record(
            session, state=_ExecutingState.EXECUTING
        )
        service = TemporalExecutionService(session)
        service.create_execution = AsyncMock()  # type: ignore[method-assign]
        service.signal_execution = AsyncMock(return_value=record)  # type: ignore[method-assign]
        service.resolve_integration_callback_target = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                SimpleNamespace(
                    workflow_id=record.workflow_id,
                    external_operation_id="ext-op-1",
                ),
                record,
            )
        )
        await service.ingest_integration_callback(
            integration_name="github",
            callback_correlation_key="key-1",
            payload={"status": "done"},
            payload_artifact_ref=None,
        )
        service.create_execution.assert_not_awaited()  # type: ignore[attr-defined]
        service.signal_execution.assert_awaited_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC-03: public-ingress HTTP journey via repository test wrappers
# ---------------------------------------------------------------------------


def test_http_create_manifest_ingest_rejected_before_service_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/executions with the retired type returns 422, no launch."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, is_superuser=True)

    def _get_mock_session() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[get_async_session] = _get_mock_session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"task": {"instructions": "copied history"}},
                "manifestArtifactRef": "artifact://manifest/historical",
                "idempotencyKey": f"ingress-{uuid4()}",
            },
        )

    assert response.status_code == 422, response.text
    assert "was retired" in response.text
    service.create_execution.assert_not_awaited()


def test_http_rerun_from_manifest_source_rejected_without_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/executions/{id}/rerun on a ManifestIngest row returns 422."""
    from api_service.api.routers import executions as executions_module
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    source = SimpleNamespace(
        workflow_id=f"mm:historical-manifest:{uuid4().hex[:8]}",
        run_id=uuid4().hex,
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        owner_type=SimpleNamespace(value="user"),
        owner_id=str(uuid4()),
        state=MoonMindWorkflowState.COMPLETED,
        parameters={"task": {"instructions": "historical manifest work"}},
        memo={},
        input_ref=None,
        plan_ref=None,
        manifest_ref="artifact://manifest/historical",
    )
    service.describe_execution.return_value = source
    service._full_rerun_parameters = Mock(
        side_effect=TemporalExecutionService._full_rerun_parameters
    )
    service.create_execution.side_effect = TemporalExecutionValidationError(
        "MoonMind.ManifestIngest was retired "
        "(MoonLadderStudios/MoonMind#4192): the new release does not "
        "register or launch manifest ingest workflows."
    )
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, is_superuser=True)
    session = AsyncMock()
    session.get.return_value = source
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        executions_module, "refresh_managed_bootstrap_snapshot", AsyncMock()
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post(f"/api/executions/{source.workflow_id}/rerun")

    assert response.status_code == 422, response.text
    assert "was retired" in response.text


# ---------------------------------------------------------------------------
# AC-04: served OpenAPI + bundle absence
# ---------------------------------------------------------------------------


def test_served_openapi_and_bundle_carry_no_manifest_authoring() -> None:
    """OpenAPI, lazy imports, and page unions expose no Manifest authoring."""
    openapi_paths: set[str] = set()
    try:
        from api_service.main import app as full_app

        spec = full_app.openapi()
        openapi_paths = set(spec.get("paths", {}).keys())
    except Exception:
        openapi_paths = set()
    assert "/api/manifests" not in openapi_paths
    assert not any(str(p).startswith("/api/manifests/") for p in openapi_paths)

    dashboard_app = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "dashboard-app.tsx"
    ).read_text(encoding="utf-8")
    assert "entrypoints/manifests" not in dashboard_app
    # Lazy page map must not reference a manifests entry module.
    assert "./manifests'" not in dashboard_app
    assert './manifests"' not in dashboard_app

    routes_ts = (
        REPO_ROOT / "frontend" / "src" / "lib" / "dashboardRoutes.ts"
    ).read_text(encoding="utf-8")
    # The 'manifest' icon-union member is a shared icon key, not a route.
    assert "'manifest'" in routes_ts or '"manifest"' in routes_ts or "manifest" in routes_ts
    assert "/manifests" not in routes_ts
    assert "manifests.tsx" not in routes_ts

    console_py = (
        REPO_ROOT / "api_service" / "api" / "routers" / "workflow_console.py"
    ).read_text(encoding="utf-8")
    # 'manifests' survives only as a reserved dashboard segment, never mounted.
    assert "task_manifest_submit_route" not in console_py
    assert "@router.get(\"/manifests" not in console_py
    assert '@router.get("/manifests' not in console_py

    schedules_tsx = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "schedules.tsx"
    ).read_text(encoding="utf-8")
    # Shared filter-grid CSS class name predates removal; no Manifest action.
    assert "Manifest Submit" not in schedules_tsx
    assert "entrypoints/manifests" not in schedules_tsx


# ---------------------------------------------------------------------------
# AC-05: schedule trigger / update / unpause closure + protected export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_retired_schedule_rejected_before_adapter_trigger(
    tmp_path: Path,
) -> None:
    """create_manual_run on a ManifestIngest definition never triggers Temporal."""
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        RecurringWorkflowsService,
    )

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            retired = RecurringWorkflowDefinition(
                name="Old Manifest",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.ManifestIngest",
                    "initialParameters": {},
                },
                policy={},
                temporal_schedule_id="mm-schedule:old-manifest",
            )
            session.add(retired)
            await session.commit()
            with pytest.raises(RecurringWorkflowValidationError, match="was retired"):
                await service.create_manual_run(retired)
            adapter.trigger_schedule.assert_not_awaited()
            adapter.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_and_unpause_retired_schedule_rejected(tmp_path: Path) -> None:
    """Retired-target updates and unpause/resume never reach the adapter."""
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        RecurringWorkflowsService,
    )

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            retired = RecurringWorkflowDefinition(
                name="Old Manifest",
                enabled=False,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.ManifestIngest",
                    "initialParameters": {},
                },
                policy={},
                temporal_schedule_id="mm-schedule:old-manifest",
            )
            session.add(retired)
            await session.commit()
            # Explicit retired-target replacement rejects before mutation.
            with pytest.raises(RecurringWorkflowValidationError, match="was retired"):
                await service.update_definition(
                    retired,
                    target={
                        "workflowType": "MoonMind.ManifestIngest",
                        "initialParameters": {},
                    },
                )
            # Unpause/resume of a retired definition rejects via the bundle
            # guard instead of re-enabling its Temporal action.
            with pytest.raises(RecurringWorkflowValidationError, match="was retired"):
                await service.update_definition(retired, enabled=True)
            adapter.unpause_schedule.assert_not_awaited()
            adapter.update_schedule.assert_not_awaited()


def test_migration_carries_export_notice_and_retires_manifest_run() -> None:
    """Migration 376 documents protected export; manifest_run never converts."""
    import re

    migration = (
        REPO_ROOT
        / "api_service"
        / "migrations"
        / "versions"
        / "376_drop_manifest_registry_4192.py"
    ).read_text(encoding="utf-8")
    assert "export" in migration.lower()
    assert "before upgrading" in migration.lower() or "before upgrade" in migration.lower()

    # Exec only the pure target-mapping section (see
    # tests/unit/scripts/test_migrate_to_temporal_schedules_4188.py): the
    # module imports runtime dependencies unavailable to a bare interpreter.
    script_source = (REPO_ROOT / "scripts" / "migrate_to_temporal_schedules.py").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"(?ms)^class RetiredManifestTargetError.*?(?=^async def migrate_definitions)",
        script_source,
    )
    assert match is not None, "retired-target mapping block missing from script"
    namespace: dict = {}
    exec(compile(match.group(0), "migrate_to_temporal_schedules.py", "exec"), namespace)
    with pytest.raises(namespace["RetiredManifestTargetError"], match="retired"):
        namespace["_workflow_type_for_target"]({"kind": "manifest_run"})
    # Ordinary definitions still convert.
    assert namespace["_workflow_type_for_target"]({"kind": "queue_task"}) == "MoonMind.UserWorkflow"


# ---------------------------------------------------------------------------
# AC-06: historical reads + ordinary flows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_manifest_detail_read_stays_available(
    tmp_path: Path,
) -> None:
    """Old ManifestIngest rows persist as replay/drain evidence; ordinary creates launch."""
    from api_service.api.routers.executions import _resolve_execution_entry
    from api_service.db.models import TemporalExecutionCanonicalRecord
    from moonmind.workflows.temporal.service import TemporalExecutionService

    async with _temporal_db(tmp_path) as session:
        record = await _insert_historical_manifest_record(session)
        # The retired row stays persisted and decodes to the historical
        # manifest entry through the production read-compat shim.
        stored = await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
        assert stored is not None
        assert _resolve_execution_entry(stored, {}) == "manifest"

        service = TemporalExecutionService(session)
        service._validate_readable_temporal_artifact_ref = AsyncMock()  # type: ignore[method-assign]
        service._client_adapter.start_workflow = AsyncMock(  # type: ignore[attr-defined]
            return_value=SimpleNamespace(run_id=f"run-{uuid4().hex[:8]}")
        )
        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="ordinary work",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"instructions": "ordinary work"}},
            idempotency_key=f"ordinary-{uuid4()}",
            _skip_pause_guard=True,
        )
        assert created.workflow_id.startswith("mm:")
        service._client_adapter.start_workflow.assert_awaited_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC-07: ordinary manifest.yaml content through the real artifact path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_artifact_upload_accepts_manifest_yaml_name(
    tmp_path: Path,
) -> None:
    """A generic artifact whose filename is manifest.yaml is ordinary content."""
    from api_service.db.models import Base
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal import (
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/artifact_gapclosure_4188.db"
    engine = create_async_engine(db_url, future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(str(tmp_path / "artifacts")),
            )
            artifact, _upload = await service.create(
                principal=f"user:{uuid4()}",
                content_type="application/x-yaml",
                size_bytes=128,
                sha256="a" * 64,
                retention_class="standard",
                link=None,
                metadata_json={"filename": "manifest.yaml"},
                encryption=None,
                redaction_level=None,
            )
            assert artifact.artifact_id
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_backend
        settings.workflow.temporal_artifact_root = original_root
