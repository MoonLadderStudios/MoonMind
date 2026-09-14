"""Manifest retirement closure — MoonLadderStudios/MoonMind#4188.

Final bounded remediation pass for the verifier gaps at ad1a00830
(AC-04 and AC-05 PARTIAL, both verification-type). Every test below
exercises a real production boundary (served OpenAPI, repo source tree,
recurring service + adapter, migration module, drain gate) rather than a
test-only model. Companion to
tests/unit/api/routers/test_manifest_retirement_gapclosure_4188.py and
tests/unit/api/routers/test_manifest_retirement_remediation_4188.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _recurring_db(tmp_path: Path):
    from api_service.db.models import Base

    db_path = tmp_path / "recurring_closure_4188.db"
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


def _retired_target() -> dict:
    return {
        "workflowType": "MoonMind.ManifestIngest",
        "initialParameters": {"task": {"instructions": "retired work"}},
    }


def _ordinary_target() -> dict:
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {"workflow": {"instructions": "ordinary work"}},
    }


def _catchup_all_policy() -> dict:
    return {
        "overlap": {"mode": "skip"},
        "catchup": {"mode": "all", "maxBackfill": 3},
        "jitterSeconds": 0,
    }


# ---------------------------------------------------------------------------
# AC-04: repo-wide frontend absence (compiled-bundle-equivalent source proof)
# ---------------------------------------------------------------------------


def _frontend_sources() -> list[Path]:
    return sorted(
        [p for p in FRONTEND_SRC.rglob("*.tsx") if p.is_file()]
        + [p for p in FRONTEND_SRC.rglob("*.ts") if p.is_file()]
    )


def test_frontend_tree_carries_no_manifest_authoring_surface() -> None:
    """No frontend source can route, import, or submit retired Manifest work.

    This is the repo-wide counterpart to the served-bundle check: the Vite
    build compiles exactly this tree through a single shared ``dashboard``
    entrypoint (see test_vite_config_has_single_dashboard_entrypoint), so
    absence here plus entrypoint singularity is absence in the built bundle.
    Documented residuals that are NOT authoring surfaces:
    - ``DashboardIconKey 'manifest'``: a shared icon key (Rows3), not a route.
    - ``_RESERVED_WORKFLOW_ROUTE_SEGMENTS`` ``manifests`` (server-side):
      prevents a workflow ID from colliding with the retired path.
    - ``manifests-filter-grid`` CSS class in schedules.tsx: shared
      filter-grid styling hook, no Manifest action.
    - ``entry: "manifest"`` historical-read fixtures and generic
      ``*manifestRef`` artifact fields: read-only evidence, ordinary content.
    - Skill/Vite ``manifest`` metadata: unrelated manifests, ordinary content.
    """
    offenders: list[str] = []
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(REPO_ROOT))
        if "entrypoints/manifests" in text:
            offenders.append(f"{rel}: entrypoints/manifests import")
        if "/api/manifests" in text:
            offenders.append(f"{rel}: /api/manifests reference")
        if "manifests.tsx" in text:
            offenders.append(f"{rel}: manifests.tsx reference")
        if "task_manifest_submit_route" in text:
            offenders.append(f"{rel}: dedicated Manifest submit route")
        if "Manifest Submit" in text:
            offenders.append(f"{rel}: Manifest Submit action")
        if "./manifests'" in text or './manifests"' in text:
            offenders.append(f"{rel}: manifests lazy page import")
    assert offenders == [], "\n".join(offenders)

    routes_ts = (FRONTEND_SRC / "lib" / "dashboardRoutes.ts").read_text(
        encoding="utf-8"
    )
    assert "/manifests" not in routes_ts

    dashboard_app = (
        FRONTEND_SRC / "entrypoints" / "dashboard-app.tsx"
    ).read_text(encoding="utf-8")
    assert "entrypoints/manifests" not in dashboard_app

    # Dead Manifest-only UI affordances are removed, not left dangling.
    placeholder = (
        FRONTEND_SRC / "components" / "dashboard" / "LoadingPlaceholder.tsx"
    ).read_text(encoding="utf-8")
    assert "'manifests'" not in placeholder
    menu = (
        FRONTEND_SRC / "components" / "DashboardSystemMenu.tsx"
    ).read_text(encoding="utf-8")
    assert "manifests: 'Data & evidence'" not in menu
    assert "manifests:" not in menu


def test_vite_config_has_single_dashboard_entrypoint() -> None:
    """The production bundle compiles one shared entrypoint: no Manifest entry."""
    import re

    vite_config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(
        encoding="utf-8"
    )
    keys = sorted(set(re.findall(r"'([a-z0-9-]+)'\s*:\s*resolve\s*\(", vite_config)))
    assert keys == ["dashboard"], keys
    # ``manifest: true`` is Vite's build-manifest option (emits
    # ``.vite/manifest.json`` for the dashboard bundle): an unrelated Vite
    # manifest, explicitly ordinary content per AC-07, not a product entry.
    assert "entrypoints/manifests" not in vite_config
    assert "/manifests" not in vite_config


def test_served_openapi_has_no_manifest_paths() -> None:
    """The served API exposes no retired Manifest registry paths."""
    from api_service.main import app as full_app

    spec = full_app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert "/api/manifests" not in paths
    assert not any(str(p).startswith("/api/manifests/") for p in paths)


# ---------------------------------------------------------------------------
# AC-05: protected export content
# ---------------------------------------------------------------------------


def test_manifest_registry_migration_documents_protected_export() -> None:
    """Migration 376 names the protected export contract, not just a notice.

    The retired ``manifest`` registry table is dropped irreversibly; the
    module must tell operators to inventory/export retained rows before
    upgrading, to restore from that pre-upgrade export (never by
    recreating an empty table), and where replay/drain evidence lives.
    """
    migration = (
        REPO_ROOT
        / "api_service"
        / "migrations"
        / "versions"
        / "376_drop_manifest_registry_4192.py"
    ).read_text(encoding="utf-8")
    lowered = migration.lower()
    assert "export" in lowered
    assert "before upgrading" in lowered or "before upgrade" in lowered
    assert "irreversible" in lowered
    assert "pre-upgrade export" in lowered
    assert "temporal visibility/memo" in lowered or "visibility/memo" in lowered

    namespace: dict = {}
    exec(
        compile(migration, "376_drop_manifest_registry_4192.py", "exec"),
        namespace,
    )
    assert namespace["revision"] == "376_drop_manifest_registry_4192"
    with pytest.raises(RuntimeError, match="pre-upgrade export"):
        namespace["downgrade"]()


# ---------------------------------------------------------------------------
# AC-05: backfill / catch-up cannot recreate retired work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_with_catchup_all_pauses_retired_without_recreate(
    tmp_path: Path,
) -> None:
    """A retired definition with catchup=all is paused, never recreated.

    Even when the Temporal Schedule is already absent (describe raises
    NotFound, the case where reconcile would normally recreate with the
    stored catch-up/backfill policy), the retired path pauses and skips
    before any create/update/describe-trigger side effect.
    """
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.workflows.temporal.schedule_errors import ScheduleNotFoundError

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            adapter.describe_schedule = AsyncMock(
                side_effect=ScheduleNotFoundError("gone")
            )
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
                target=_retired_target(),
                policy=_catchup_all_policy(),
                temporal_schedule_id="mm-schedule:old-manifest",
            )
            session.add(retired)
            await session.commit()
            await service.reconcile_schedules(limit=10)
            adapter.pause_schedule.assert_awaited_once()
            adapter.create_schedule.assert_not_awaited()
            adapter.update_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_action_current_rejects_retired_before_describe(
    tmp_path: Path,
) -> None:
    """Catch-up/backfill normalization never reaches Temporal for retired targets."""
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
                target=_retired_target(),
                policy=_catchup_all_policy(),
                temporal_schedule_id="mm-schedule:old-manifest",
            )
            session.add(retired)
            await session.commit()
            with pytest.raises(
                RecurringWorkflowValidationError, match="was retired"
            ):
                await service._ensure_schedule_action_current(retired)
            adapter.describe_schedule.assert_not_awaited()
            adapter.update_schedule.assert_not_awaited()
            adapter.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_definition_with_catchup_all_rejects_retired_before_persist(
    tmp_path: Path,
) -> None:
    """A retired create with catchup=all rejects before persistence/dispatch."""
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        RecurringWorkflowsService,
    )
    from sqlalchemy import select

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            with pytest.raises(
                RecurringWorkflowValidationError, match="was retired"
            ):
                await service.create_definition(
                    name="Retired Manifest",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="0 6 * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target=_retired_target(),
                    policy=_catchup_all_policy(),
                    agent_profile_selection=None,
                    actor=None,
                )
            adapter.create_schedule.assert_not_awaited()
            rows = (
                await session.execute(select(RecurringWorkflowDefinition))
            ).scalars().all()
            assert rows == []


# ---------------------------------------------------------------------------
# AC-05: queued-start drain — pause is not proof an execution stopped
# ---------------------------------------------------------------------------


def test_drain_gate_pause_does_not_imply_stopped_execution() -> None:
    """An already-created ManifestIngest execution needs explicit cancel.

    Reconcile pauses the retired Temporal Schedule producer, but a pause
    does not stop an already-created (running) execution. The drain gate
    stays closed while open histories exist and opens only when every
    dimension — open histories, pending tasks, existing schedules — is
    observed at zero. Unobservable dimensions fail closed.
    """
    from moonmind.gates.manifest_ingest_drain import (
        collect_manifest_ingest_drain_observations,
        evaluate_manifest_ingest_drain_observations,
    )

    # Schedule paused/removed but its execution still running: retain.
    running = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=1,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert running.may_deploy_removal is False
    assert running.required_action == "retain_and_drain"
    assert "open_manifest_ingest_histories" in running.blocking_dimensions

    # Pending retired activity tasks also block removal.
    pending = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=2,
            existing_manifest_schedules=0,
        )
    )
    assert pending.may_deploy_removal is False

    # Unobservable visibility fails closed: never authorize removal.
    unknown = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=None,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert unknown.may_deploy_removal is False

    # Fully drained: all dimensions observed at zero.
    drained = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert drained.may_deploy_removal is True
    assert drained.required_action == "safe_to_remove"


# ---------------------------------------------------------------------------
# AC-05: ordinary-schedule trigger / backfill regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_schedule_trigger_and_backfill_still_run(
    tmp_path: Path,
) -> None:
    """Ordinary definitions still trigger and recreate with catch-up policy."""
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.workflows.temporal.schedule_errors import ScheduleNotFoundError
    from moonmind.workflows.temporal.client import ScheduleTriggerResult

    async with _recurring_db(tmp_path) as maker:
        async with maker() as session:
            adapter = _mock_adapter()
            adapter.trigger_schedule.return_value = ScheduleTriggerResult(
                workflow_id="ordinary-workflow",
                run_id="ordinary-run",
                disposition="started",
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            definition = RecurringWorkflowDefinition(
                name="Ordinary work",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                owner_user_id=uuid4(),
                target=_ordinary_target(),
                policy=_catchup_all_policy(),
                temporal_schedule_id="mm-schedule:ordinary",
            )
            session.add(definition)
            await session.commit()

            # Manual trigger path works for ordinary definitions.
            run = await service.create_manual_run(definition)
            adapter.trigger_schedule.assert_awaited_once()
            assert run.outcome.value == "enqueued"
            assert run.temporal_workflow_id == "ordinary-workflow"
            assert run.temporal_run_id == "ordinary-run"

            # Missing Temporal Schedule with catch-up policy is recreated.
            adapter2 = _mock_adapter()
            adapter2.describe_schedule = AsyncMock(
                side_effect=ScheduleNotFoundError("gone")
            )
            service2 = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter2
            )
            await service2.reconcile_schedules(limit=10)
            adapter2.create_schedule.assert_awaited_once()
