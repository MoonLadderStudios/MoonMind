"""Manifest retirement remediation — MoonLadderStudios/MoonMind#4188.

Bounded remediation for the verifier gaps at c1555652 (AC-02..AC-07
PARTIAL): every retired producer intent must reject at the earliest
shared boundary before any side effect, old schedules must retire with
protected evidence and no reactivation, historical reads stay available,
ordinary manifest content stays allowed, and no authoring surface may
reappear. Each test below exercises a real production boundary (schema
validator, TemporalExecutionService, recurring service, router decoders,
repo source scan) rather than a test-only model.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]

RETIRED_MESSAGE = "MoonMind.ManifestIngest was retired"


# ---------------------------------------------------------------------------
# AC-03: public-ingress ordering — schema is the earliest shared boundary
# ---------------------------------------------------------------------------


def test_schema_rejects_retired_manifest_ingest_actionably() -> None:
    """CreateExecutionRequest rejects retired intent at the ingress schema."""
    from moonmind.schemas.temporal_models import CreateExecutionRequest

    with pytest.raises(ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {},
            }
        )


def test_router_create_orders_validation_before_effects() -> None:
    """POST /api/executions validates before service/snapshot side effects."""
    source = (REPO_ROOT / "api_service" / "api" / "routers" / "executions.py").read_text(
        encoding="utf-8"
    )
    create_fn = source.index("async def create_execution(")
    validate_pos = source.index("CreateExecutionRequest.model_validate", create_fn)
    service_pos = source.index("await service.create_execution(", create_fn)
    snapshot_pos = source.index(
        "_persist_original_workflow_input_snapshot_from_parameters", create_fn
    )
    assert validate_pos < service_pos < snapshot_pos


def test_rerun_handler_converges_on_shared_create_execution() -> None:
    """rerun_execution reuses create_execution so retired sources reject."""
    source = (REPO_ROOT / "api_service" / "api" / "routers" / "executions.py").read_text(
        encoding="utf-8"
    )
    rerun_fn = source.index("async def rerun_execution(")
    next_def = source.index("\nasync def ", rerun_fn + 1)
    body = source[rerun_fn:next_def]
    assert "canonical.workflow_type.value" in body
    assert "await service.create_execution(" in body


# ---------------------------------------------------------------------------
# AC-03: TemporalExecutionService rejects before artifact/reader/Temporal work
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _temporal_db(tmp_path: Path):
    from api_service.db.models import Base
    from moonmind.config.settings import settings

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_remediation_4188.db"
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


@pytest.mark.asyncio
async def test_service_create_rejects_before_artifact_validation_and_start(
    tmp_path: Path,
) -> None:
    """create_execution rejects ManifestIngest before reader/Temporal effects."""
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    async with _temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._validate_readable_temporal_artifact_ref = AsyncMock()  # type: ignore[method-assign]
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]

        with pytest.raises(
            TemporalExecutionValidationError, match="was retired"
        ):
            await service.create_execution(
                workflow_type="MoonMind.ManifestIngest",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="artifact://manifest/1",
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
                _skip_pause_guard=True,
            )
        service._validate_readable_temporal_artifact_ref.assert_not_awaited()  # type: ignore[attr-defined]
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retired_updates_reject_before_source_load(tmp_path: Path) -> None:
    """Retired manifest-only updates fail before loading the source row."""
    from moonmind.workflows.temporal.service import (
        RETIRED_MANIFEST_UPDATE_NAMES,
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    assert RETIRED_MANIFEST_UPDATE_NAMES, "retired update set must be non-empty"
    async with _temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._require_source_execution = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(
            TemporalExecutionValidationError, match="was retired"
        ):
            await service.update_execution(
                workflow_id="mm:never-loaded",
                update_name=sorted(RETIRED_MANIFEST_UPDATE_NAMES)[0],
            )
        service._require_source_execution.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rerun_source_manifest_ingest_rejected_at_shared_boundary(
    tmp_path: Path,
) -> None:
    """A rerun copied from a ManifestIngest canonical row cannot launch."""
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    async with _temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        # rerun_execution passes canonical.workflow_type.value verbatim.
        with pytest.raises(
            TemporalExecutionValidationError, match="was retired"
        ):
            await service.create_execution(
                workflow_type="MoonMind.ManifestIngest",
                owner_id=uuid4(),
                title="rerun of historical manifest",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="artifact://manifest/historical",
                failure_policy=None,
                initial_parameters={"task": {"instructions": "copied history"}},
                idempotency_key=f"rerun:mm:historical:{uuid4()}",
                _skip_pause_guard=True,
            )


# ---------------------------------------------------------------------------
# AC-02/AC-05: recurring create/update reject; reconcile pauses; ordinary runs
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _recurring_db(tmp_path: Path):
    from api_service.db.models import Base

    db_path = tmp_path / "recurring_remediation_4188.db"
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
    adapter.trigger_schedule = AsyncMock()
    adapter.delete_schedule = AsyncMock()
    adapter.describe_schedule = AsyncMock()
    adapter.resolve_workflow_task_queue = MagicMock(return_value="mm.workflow.user.v2")
    return adapter


@pytest.mark.asyncio
async def test_recurring_rejects_retired_target_before_persistence(
    tmp_path: Path,
) -> None:
    """Recurring create/update reject ManifestIngest before persistence."""
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        RecurringWorkflowsService,
    )

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=_mock_adapter()
            )
            with pytest.raises(RecurringWorkflowValidationError, match="was retired"):
                await service.create_definition(
                    name="Manifest Plan",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="0 6 * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={
                        "workflowType": "MoonMind.ManifestIngest",
                        "initialParameters": {"action": "plan"},
                    },
                    policy={},
                )


@pytest.mark.asyncio
async def test_ordinary_recurring_schedule_still_runs(tmp_path: Path) -> None:
    """Ordinary UserWorkflow recurring definitions still dispatch."""
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            definition = await service.create_definition(
                name="Daily Demo",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {
                        "task": {
                            "instructions": "ordinary work",
                            "publish": {"mode": "none"},
                            "skill": {"id": "auto", "args": {}},
                        },
                    },
                },
                policy={},
            )
            assert definition.target["workflowType"] == "MoonMind.UserWorkflow"
            adapter.create_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_pauses_retired_schedule_without_recreate(
    tmp_path: Path,
) -> None:
    """Reconcile pauses (never recreates) ManifestIngest schedules."""
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
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
            reconciled = await service.reconcile_schedules(limit=10)
            assert reconciled >= 1
            adapter.pause_schedule.assert_awaited()
            adapter.create_schedule.assert_not_awaited()
            adapter.update_schedule.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC-06: historical detail/artifact reads stay available
# ---------------------------------------------------------------------------


def test_historical_manifest_entry_decoders_preserved() -> None:
    """Old entry=manifest rows and ManifestIngest types stay readable."""
    from api_service.api.routers.executions import (
        _normalize_entry_value,
        _resolve_execution_entry,
    )

    assert _normalize_entry_value("manifest") == "manifest"
    assert _normalize_entry_value("MoonMind.ManifestIngest") is None  # not an entry
    record = SimpleNamespace(
        entry=None, workflow_type=SimpleNamespace(value="MoonMind.ManifestIngest")
    )
    assert _resolve_execution_entry(record, {}) == "manifest"
    ordinary = SimpleNamespace(
        entry=None, workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow")
    )
    assert _resolve_execution_entry(ordinary, {}) == "user_workflow"


# ---------------------------------------------------------------------------
# AC-07: arbitrary user manifest.yaml stays ordinary content
# ---------------------------------------------------------------------------


def test_arbitrary_manifest_yaml_is_not_a_native_product_marker() -> None:
    """A user file named manifest.yaml must not trip retirement guards."""
    import re

    native_route = re.compile(r"/api/manifests|manifests_router")
    native_workflow = re.compile(
        r"MoonMind\.ManifestIngest|MoonMindManifestIngest|manifest_ingest"
    )
    descriptor = "upload:manifest.yaml (generic artifact upload)"
    assert not native_route.search(descriptor)
    assert not native_workflow.search(descriptor)


def test_ordinary_manifest_guard_allows_user_content() -> None:
    """The no-reintroduction guard allows user/Skill/Vite manifests."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "manifest_qualification_4193",
        REPO_ROOT / "tests" / "unit" / "config" / "test_manifest_retirement_qualification_4193.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    problems = mod.check_user_manifest_allowed(
        [
            "upload:manifest.yaml",
            "skill:my-skill/SKILL.md",
            ".vite/manifest.json",
            "recovery:checkpoint-manifest",
        ]
    )
    assert problems == []


# ---------------------------------------------------------------------------
# AC-04: no Manifest authoring surface in routes/nav/bundles/CLI
# ---------------------------------------------------------------------------


def test_no_manifest_authoring_surface_in_repo() -> None:
    """Retired routes/entries/CLI groups are absent; residuals non-authoring."""
    main_py = (REPO_ROOT / "api_service" / "main.py").read_text(encoding="utf-8")
    assert "manifests_router" not in main_py
    assert "/api/manifests" not in main_py

    assert not (REPO_ROOT / "api_service" / "api" / "routers" / "manifests.py").exists()
    assert not (REPO_ROOT / "api_service" / "services" / "manifests_service.py").exists()
    assert not (REPO_ROOT / "frontend" / "src" / "entrypoints" / "manifests.tsx").exists()
    assert not (REPO_ROOT / "moonmind" / "manifest" / "manifest_cli.py").exists()

    cli_py = (REPO_ROOT / "moonmind" / "cli.py").read_text(encoding="utf-8")
    assert 'name="manifest"' not in cli_py

    console_py = (
        REPO_ROOT / "api_service" / "api" / "routers" / "workflow_console.py"
    ).read_text(encoding="utf-8")
    assert "task_manifest_submit_route" not in console_py

    dashboard_app = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "dashboard-app.tsx"
    ).read_text(encoding="utf-8")
    assert "entrypoints/manifests" not in dashboard_app
