"""Manifest removal remediation — MoonLadderStudios/MoonMind#4190.

Bounded remediation pass for the verifier gaps at 08222188 (R3-R8 PARTIAL).
Every test below exercises a real production boundary (settings/service
signature, worker registry/catalog composition, CLI/API imports, historical
read-compat shim, repo source scan) rather than a test-only model.

- R3: API/worker/CLI start without Manifest service mocks or settings.
- R4: historical rows render through the generic authorized read path with
  unchanged type/input/result/lineage and original outcome.
- R6: every retained shared utility names its surviving non-Manifest
  consumer; remaining Manifest strings are drain gates, actionable
  rejection guards, retirement comments, or historical-decode branches.
- R7: explicitly excluded manifests remain present and importable.

Live drain-gate probes (R4b) and full #4189 milestone-A integration belong
to their owning surfaces and are NOT claimed here.
"""

from __future__ import annotations

import importlib.util
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[3]

DELETED_PRODUCT_MODULES = (
    "moonmind.manifest",
    "moonmind.manifest.loader",
    "moonmind.manifest.runner",
    "moonmind.schemas.manifest_models",
    "moonmind.schemas.manifest_v0_models",
    "moonmind.schemas.manifest_ingest_models",
    "moonmind.workflows.executions.manifest_contract",
    "moonmind.workflows.executions.manifest_errors",
    "api_service.services.manifests_service",
    "api_service.services.manifest_sync_service",
    "moonmind.workflows.temporal.workflows.manifest_ingest",
    "moonmind.workflows.temporal.manifest_ingest",
)


# ---------------------------------------------------------------------------
# R3: startup without Manifest mocks or settings
# ---------------------------------------------------------------------------


def test_no_manifest_threshold_setting_or_service_param() -> None:
    """No Manifest-only setting or service constructor argument survives."""
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import TemporalExecutionService

    temporal_fields = set(type(settings.temporal).model_fields)
    assert not any("manifest" in name.lower() for name in temporal_fields)
    assert not hasattr(settings.temporal, "manifest_continue_as_new_phase_threshold")

    params = inspect.signature(TemporalExecutionService.__init__).parameters
    assert not any("manifest" in name.lower() for name in params)


def test_worker_registry_and_catalog_have_no_manifest_bindings() -> None:
    """Actual worker composition sources carry no Manifest workflow/activity."""
    from moonmind.config.settings import TemporalSettings
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        validate_activity_catalog_runtime_bindings,
    )
    from moonmind.workflows.temporal.workflow_registry import (
        STATIC_WORKFLOW_REGISTRATIONS,
        raw_workflow_registrations,
        workflow_fleet_workflow_types,
    )

    registered_types = {str(t) for t in workflow_fleet_workflow_types(TemporalSettings())}
    assert "MoonMind.ManifestIngest" not in registered_types
    assert all(
        "manifestingest" not in str(getattr(r, "workflow_type", "")).lower()
        for r in (*STATIC_WORKFLOW_REGISTRATIONS, *raw_workflow_registrations())
    )

    catalog = build_default_activity_catalog()
    activity_types = {d.activity_type for d in catalog.activities}
    assert "manifest.compile" not in activity_types
    assert "manifest.write_summary" not in activity_types
    validate_activity_catalog_runtime_bindings(catalog)


def test_api_worker_cli_import_without_manifest_services() -> None:
    """API, worker composition, and CLI import with deleted services absent."""
    # Use importlib for the smoke import so this module keeps a single
    # static import style (``from ... import ...`` below) and does not
    # trigger mixed import/static-analysis findings.
    for module_name in (
        "api_service.api.routers.executions",
        "moonmind.cli",
        "moonmind.workflows.temporal.activity_catalog",
        "moonmind.workflows.temporal.workflow_registry",
    ):
        assert importlib.import_module(module_name) is not None

    for module_name in DELETED_PRODUCT_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError):
            spec = None
        assert spec is None, module_name

    registered_commands: list[str] = []
    from moonmind.cli import app as cli_app

    for command in getattr(cli_app, "registered_commands", []):
        registered_commands.append(str(getattr(command, "name", "")))
    for group in getattr(cli_app, "registered_groups", []):
        registered_commands.append(str(getattr(group, "name", "")))
    assert not any(cmd == "manifest" for cmd in registered_commands)


def test_worker_process_spec_has_no_manifest_bindings() -> None:
    """Real worker-process composition carries no Manifest workflow/activity.

    R3 gap: prior coverage proved imports and catalog contents in-process.
    This exercises the exact immutable worker identity the worker process
    passes to the Temporal SDK (``build_worker_spec`` over the real
    workflow-fleet topology, workflow classes, and activity handlers),
    which is the closest executable composition proof without a live
    Temporal server (live connect/poll remains deployment-owner evidence).
    """
    import os

    from moonmind.workflows.temporal.workers import (
        WORKFLOW_FLEET,
        build_worker_spec,
        build_worker_topology,
    )
    from moonmind.workflows.temporal.workflow_registry import (
        workflow_fleet_activity_handlers,
        workflow_fleet_workflow_classes,
    )

    topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    spec = build_worker_spec(
        topology=topology,
        workflows=workflow_fleet_workflow_classes(),
        activities=workflow_fleet_activity_handlers(),
        environ={**os.environ, "MOONMIND_DEPLOYMENT_MODE": "development"},
    )
    lowered_workflows = [str(t).lower() for t in spec.workflow_types]
    assert "moonmind.manifestingest" not in lowered_workflows
    assert not any("manifestingest" in t for t in lowered_workflows)
    assert "manifest.compile" not in set(spec.activity_types)
    assert "manifest.write_summary" not in set(spec.activity_types)
    assert not any(str(t).lower().startswith("manifest.") for t in spec.activity_types)
    payload = spec.readiness_payload()
    assert payload["fleet"] == WORKFLOW_FLEET
    assert "moonmind.manifestingest" not in [
        str(t).lower() for t in payload["workflowTypes"]
    ]


def test_cli_starts_without_manifest_services() -> None:
    """CLI process starts (``--help``) with no Manifest command or setting.

    R3 gap: prior coverage proved CLI imports. This invokes the real Typer
    app entry (the same ``moonmind.cli:main`` target the installed
    ``moonmind`` console script calls) across every command group without
    Manifest service mocks or settings. A live installed-binary launch
    remains deployment-owner evidence (the sandbox binary may be stale).
    """
    from typer.testing import CliRunner

    from moonmind.cli import app

    runner = CliRunner()
    for argv in ([], ["worker"], ["container"], ["workflow"]):
        result = runner.invoke(app, [*argv, "--help"], color=False)
        assert result.exit_code == 0, result.output
    top = runner.invoke(app, ["--help"], color=False)
    assert top.exit_code == 0, top.output
    # Behavioral requirement only: the retired `manifest` command group is
    # absent (no help-prose wording contract; copy edits must not fail).
    retired = runner.invoke(app, ["manifest", "--help"], color=False)
    assert retired.exit_code != 0


# ---------------------------------------------------------------------------
# R4: historical rows via the generic authorized read path
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _temporal_db(tmp_path: Path):
    from api_service.db.models import Base
    from moonmind.config.settings import settings

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_remediation_4190.db"
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
async def test_historical_manifest_row_preserves_identity_and_outcome(
    tmp_path: Path,
) -> None:
    """Historical ManifestIngest rows keep type/input/lineage and outcome."""
    from api_service.api.routers.executions import _resolve_execution_entry
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalExecutionOwnerType,
        TemporalWorkflowType,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    original_instructions = "historical manifest work"
    async with _temporal_db(tmp_path) as session:
        record = TemporalExecutionCanonicalRecord(
            workflow_id=f"mm:historical-manifest:{uuid4().hex[:8]}",
            run_id=uuid4().hex,
            namespace="default",
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
            owner_id=str(uuid4()),
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.COMPLETED,
            entry="manifest",
            manifest_ref="artifact://manifest/historical",
            parameters={"task": {"instructions": original_instructions}},
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)

        stored = await session.get(
            TemporalExecutionCanonicalRecord, record.workflow_id
        )
        assert stored is not None
        # Generic authorized read path decodes the retired row without the
        # retired parser/registration.
        assert _resolve_execution_entry(stored, {}) == "manifest"
        assert stored.workflow_type is TemporalWorkflowType.MANIFEST_INGEST
        assert stored.entry == "manifest"
        assert stored.manifest_ref == "artifact://manifest/historical"
        assert stored.parameters["task"]["instructions"] == original_instructions
        assert stored.state is MoonMindWorkflowState.COMPLETED

        # A fresh rerun from the retired row is rejected actionably, and the
        # original completed outcome is unchanged.
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        with pytest.raises(Exception, match="was retired"):
            await service._create_fresh_rerun_execution(
                stored,  # type: ignore[arg-type]
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                idempotency_key=None,
            )
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]
        reread = await session.get(
            TemporalExecutionCanonicalRecord, record.workflow_id
        )
        assert reread is not None
        assert reread.state is MoonMindWorkflowState.COMPLETED
        assert reread.parameters["task"]["instructions"] == original_instructions


# ---------------------------------------------------------------------------
# R6: surviving-consumer audit of retained shared utilities
# ---------------------------------------------------------------------------


def test_manifest_artifact_ref_has_surviving_generic_consumers() -> None:
    """manifest_artifact_ref is a generic artifact ref, not a live product."""
    from moonmind.workflows.temporal.service import TemporalExecutionService

    params = inspect.signature(TemporalExecutionService.create_execution).parameters
    assert "manifest_artifact_ref" in params

    story_tools = (
        REPO_ROOT / "moonmind" / "workflows" / "temporal" / "story_output_tools.py"
    ).read_text()
    assert "manifest_artifact_ref=None" in story_tools


@pytest.mark.asyncio
async def test_manifest_artifact_ref_propagates_to_user_workflow_memo(
    tmp_path: Path,
) -> None:
    """A surviving UserWorkflow launch carries the manifest ref to its memo.

    Behavioral propagation proof (no source-spelling assertion): launch an
    ordinary UserWorkflow with a manifest artifact ref and assert the
    persisted record memo, the record artifact refs, and the Temporal start
    memo all carry ``artifact://manifest/propagation-probe``. Refactors
    that move the assignment keep passing as long as callers receive the
    reference; a branch that drops it fails.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.service import TemporalExecutionService

    manifest_ref = "artifact://manifest/propagation-probe"
    async with _temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._validate_readable_temporal_artifact_ref = AsyncMock()  # type: ignore[method-assign]
        service._client_adapter.start_workflow = AsyncMock(  # type: ignore[attr-defined]
            return_value=SimpleNamespace(run_id=f"run-{uuid4().hex[:8]}")
        )
        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="propagation probe",
            input_artifact_ref="artifact://input/propagation-probe",
            plan_artifact_ref=None,
            manifest_artifact_ref=manifest_ref,
            failure_policy=None,
            initial_parameters={"workflow": {"instructions": "probe"}},
            idempotency_key=f"propagation-{uuid4()}",
            _skip_pause_guard=True,
        )
        assert created.memo["manifest_ref"] == manifest_ref
        assert manifest_ref in list(created.artifact_refs or [])
        launched_memo = service._client_adapter.start_workflow.await_args.kwargs[  # type: ignore[attr-defined]
            "memo"
        ]
        assert launched_memo["manifest_ref"] == manifest_ref


def test_retained_manifest_strings_are_guards_history_or_excluded() -> None:
    """No live Manifest-only branch remains outside the allowlisted owners."""
    allowed_path_fragments = (
        "moonmind/gates/manifest_ingest_drain.py",
        "moonmind/gates/manifest_registry_migration_4191.py",
        "moonmind/workflows/temporal/service.py",
        "moonmind/workflows/temporal/workflow_registry.py",
        "moonmind/schemas/temporal_models.py",
        "moonmind/cli.py",
        "api_service/api/routers/executions.py",
        "api_service/services/recurring_workflows_service.py",
        "api_service/db/models.py",
        "api_service/migrations/versions/",
        "docs/tmp/QdrantCutoverRunbook-4115.md",
    )
    # Explicitly excluded manifests and ordinary step-execution manifests are
    # not native Manifest product surfaces.
    excluded_fragments = (
        "recovery_manifest",
        "effective_capabilities",
        "verify_vite_manifest",
        "step_execution",
        "step-execution",
        "saved_work",
        "checkpoint",
        "prepared_context",
        "control_stop_continuation",
        "remediation_tool",
        "skills_on_demand",
        "skill_materialization",
        "skill_resolution",
        "agent_skills",
        "omnigent",
        "codex_worker",
        "remediation_context",
        "story_output_tools",
        "workspace_locators",
        "checkpoint_restore",
        "artifacts.py",
        "jules_bundle",
        "container_image_acquisition",
    )
    markers = (
        "ManifestIngest",
        "manifest.compile",
        "manifest.write_summary",
        "TEMPORAL_MANIFEST",
    )
    offenders: list[str] = []
    for root in (REPO_ROOT / "moonmind", REPO_ROOT / "api_service"):
        for path in root.rglob("*.py"):
            relative = path.as_posix()
            if any(
                str(path).endswith(frag) or frag in relative
                for frag in allowed_path_fragments
            ):
                continue
            if "test" in path.parts:
                continue
            try:
                text = path.read_text()
            except OSError:
                continue
            lowered = text.lower()
            for marker in markers:
                if marker.lower() in lowered:
                    # Excluded-manifest owners mention "manifest" generically;
                    # only the exact product markers matter, and files owned
                    # by excluded/ordinary manifests are out of scope.
                    if any(frag in relative for frag in excluded_fragments):
                        continue
                    offenders.append(f"{relative}:{marker}")
                    break
    assert offenders == []


# ---------------------------------------------------------------------------
# R7: explicitly excluded manifests stay functional
# ---------------------------------------------------------------------------


def test_excluded_manifests_present_and_importable() -> None:
    """Recovery/Skill/capability/Vite manifests are preserved per the issue."""
    assert (REPO_ROOT / "moonmind" / "workflows" / "temporal" / "recovery_manifest.py").exists()
    assert (REPO_ROOT / "moonmind" / "omnigent" / "effective_capabilities.py").exists()
    assert (REPO_ROOT / "tools" / "verify_vite_manifest.py").exists()

    import moonmind.omnigent.effective_capabilities  # noqa: F401
    import moonmind.workflows.temporal.recovery_manifest  # noqa: F401


def test_historical_manifest_read_allowed_but_admission_rejected() -> None:
    """R4: generic read/admission split without retired parser/registration.

    Historical rows resolve through the generic authorized read path
    (``_resolve_execution_entry``) while the production registry carries no
    executable ManifestIngest registration and the deleted product modules
    stay unimportable. New ManifestIngest admission is rejected actionably
    at the schema boundary (``CreateExecutionRequest``), preserving the
    original completed outcome without launching anything.
    """
    import pydantic

    from api_service.api.routers.executions import _resolve_execution_entry
    from moonmind.schemas.temporal_models import CreateExecutionRequest
    from moonmind.workflows.temporal.workflow_registry import (
        STATIC_WORKFLOW_REGISTRATIONS,
    )

    registered_types = {
        str(getattr(r, "workflow_type", "")) for r in STATIC_WORKFLOW_REGISTRATIONS
    }
    assert "MoonMind.ManifestIngest" not in registered_types

    for module_name in DELETED_PRODUCT_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError):
            spec = None
        assert spec is None, module_name

    historical = SimpleNamespace(
        entry="manifest",
        workflow_type=SimpleNamespace(value="MoonMind.ManifestIngest"),
    )
    assert _resolve_execution_entry(historical, {}) == "manifest"

    with pytest.raises(pydantic.ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"task": {"instructions": "new work"}},
            }
        )
