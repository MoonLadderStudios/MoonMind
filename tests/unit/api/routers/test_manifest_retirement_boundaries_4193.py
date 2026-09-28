"""Manifest retirement boundaries — MoonLadderStudios/MoonMind#4193.

Bounded integration coverage through actual product boundaries for the
landed Manifest removal. Reuses the existing hermetic Temporal/artifact
fixtures (SQLite + local_fs + mocked Temporal adapter) instead of
repeating every lifecycle fault across every harness and old release.
PostgreSQL preservation/migration and browser journeys are owned by the
persistence owner and existing normal-workflow suites; they are referenced,
not rebuilt, here.

Covers the assessment gaps (PARTIALLY_IMPLEMENTED at e281a95):

- R1: production worker registration + mounted routes + retired
  submission/control rejection with zero consequential effects.
- R2: arbitrary user manifest.yaml / Skill / Vite / provenance manifests
  remain ordinary supported content at the owning artifact/guard boundary.
- R3: generic historical access for both original input meanings
  (manifest_ref compile histories, manifestArtifactRef node histories)
  with degraded metadata; readable but not replayable; no broadened
  product projection.
- R5: existing impact selection routes these boundaries to required CI;
  empty input selects the full gate so missing execution cannot pass.
- R6: a representative normal UserWorkflow launches without
  Manifest/vector setup at the runtime boundary.

Does not build a retirement verification platform, restore an executable
Manifest package, or require live-deployment inventory (#4189 owns
per-deployment observation and cutover separately).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]


@asynccontextmanager
async def _temporal_db(tmp_path: Path):
    from api_service.db.models import Base
    from moonmind.config.settings import settings

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_boundaries_4193.db"
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


async def _insert_historical_manifest_record(session) -> object:
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
        state=MoonMindWorkflowState.COMPLETED,
        entry="manifest",
        manifest_ref="artifact://manifest/historical",
        parameters={"task": {"instructions": "historical manifest work"}},
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# R1: production worker registration (not a regex helper)
# ---------------------------------------------------------------------------


def test_production_worker_registry_has_no_manifest_ingest() -> None:
    """The exact production worker composition registers no Manifest work."""
    from moonmind.workflows.temporal.workflow_registry import (
        product_workflow_types,
        workflow_fleet_activity_handlers,
        workflow_fleet_workflow_classes,
    )
    from moonmind.workflows.temporal.workers import (
        list_registered_workflow_types,
    )
    from temporalio import activity, workflow

    classes = workflow_fleet_workflow_classes()
    temporal_names = [
        workflow._Definition.must_from_class(cls).name for cls in classes
    ]
    assert "MoonMind.ManifestIngest" not in temporal_names
    assert not any("anifest" in name for name in temporal_names)

    assert "MoonMind.ManifestIngest" not in product_workflow_types()
    assert "MoonMind.ManifestIngest" not in list_registered_workflow_types()
    # Retirement invariant only: future supported workflows may join the
    # catalog without touching this Manifest fixture.
    assert "MoonMind.UserWorkflow" in product_workflow_types()

    handler_names = [
        activity._Definition.must_from_callable(handler).name
        for handler in workflow_fleet_activity_handlers()
    ]
    assert not any("manifest_compile" in name for name in handler_names)
    assert not any("manifest_write_summary" in name for name in handler_names)
    assert not any("TemporalManifest" in name for name in handler_names)


# ---------------------------------------------------------------------------
# R1: mounted production routes (not an isolated router)
# ---------------------------------------------------------------------------


def test_mounted_production_routes_expose_no_manifest_authoring() -> None:
    """The mounted production app serves executions but no Manifest registry."""
    from fastapi.testclient import TestClient

    from api_service.main import app as production_app

    # The served OpenAPI is the mounted-route contract: it is generated from
    # the production app's included routers (263 paths in this checkout,
    # including /api/executions). Direct app.routes introspection is not
    # used: middleware instrumentation wraps the route table while openapi()
    # still renders the served contract (see closure-suite precedent).
    spec = production_app.openapi()
    openapi_paths = set(spec.get("paths", {}).keys())
    assert "/api/executions" in openapi_paths
    assert "/api/manifests" not in openapi_paths
    assert not any(str(p).startswith("/api/manifests/") for p in openapi_paths)
    # OpenAPI omits include_in_schema=False routes, so probe the served app:
    # retired authoring paths must return the ordinary not-found response.
    client = TestClient(production_app, raise_server_exceptions=False)
    for retired_path in ("/api/manifests", "/api/manifests/anything"):
        response = client.get(retired_path)
        assert response.status_code == 404, retired_path


# ---------------------------------------------------------------------------
# R1: retired submission/control rejects before consequential effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retired_submission_rejects_before_consequential_effects(
    tmp_path: Path,
) -> None:
    """Retired create/update reject before reader/network/launch effects."""
    from unittest.mock import patch

    from moonmind.workflows.temporal.service import (
        RETIRED_MANIFEST_UPDATE_NAMES,
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    assert RETIRED_MANIFEST_UPDATE_NAMES, "retired update set must be non-empty"
    async with _temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._validate_readable_temporal_artifact_ref = AsyncMock()  # type: ignore[method-assign]
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        # Stale legacy runtime/profile must not mask the retirement rejection:
        # retirement is checked before provider-profile resolution.
        legacy_parameters = {
            "task": {
                "instructions": "retired",
                "targetRuntime": "stale-runtime",
                "providerProfileRef": "stale-profile",
            },
            "targetRuntime": "stale-runtime",
        }
        with patch(
            "moonmind.workflows.temporal.service."
            "require_launch_target_provider_profile_runtime",
            new=AsyncMock(),
        ) as provider_guard:
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
                    initial_parameters=legacy_parameters,
                    idempotency_key=f"retired-{uuid4()}",
                    _skip_pause_guard=True,
                )
            provider_guard.assert_not_awaited()
        service._validate_readable_temporal_artifact_ref.assert_not_awaited()  # type: ignore[attr-defined]
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]

        service._require_source_execution = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(
            TemporalExecutionValidationError, match="was retired"
        ):
            await service.update_execution(
                workflow_id="mm:never-loaded",
                update_name=sorted(RETIRED_MANIFEST_UPDATE_NAMES)[0],
            )
        service._require_source_execution.assert_not_awaited()  # type: ignore[attr-defined]


def test_retired_intent_rejects_at_ingress_schema() -> None:
    """The shared ingress schema rejects retired intent before any effect."""
    from pydantic import ValidationError

    from moonmind.schemas.temporal_models import CreateExecutionRequest

    with pytest.raises(ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"task": {"instructions": "retired"}},
                "manifestArtifactRef": "artifact://manifest/historical",
            }
        )


# ---------------------------------------------------------------------------
# R2: ordinary supported content at the owning boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_manifest_yaml_flows_as_supported_content(
    tmp_path: Path,
) -> None:
    """A generic artifact named manifest.yaml is ordinary supported content."""
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
    db_url = f"sqlite+aiosqlite:///{tmp_path}/ordinary_manifest_4193.db"
    engine = create_async_engine(db_url, future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            import hashlib

            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(str(tmp_path / "artifacts")),
            )
            payload = b"name: ordinary-manifest\nkind: generic-artifact\n"
            digest = hashlib.sha256(payload).hexdigest()
            principal = f"user:{uuid4()}"
            artifact, _upload = await service.create(
                principal=principal,
                content_type="application/x-yaml",
                size_bytes=len(payload),
                sha256=digest,
                retention_class="standard",
                link=None,
                metadata_json={"filename": "manifest.yaml"},
                encryption=None,
                redaction_level=None,
            )
            assert artifact.artifact_id
            completed = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=principal,
                payload=payload,
                content_type="application/x-yaml",
            )
            assert completed.artifact_id == artifact.artifact_id
            _read_artifact, read_bytes = await service.read(
                artifact_id=artifact.artifact_id,
                principal=principal,
            )
            assert read_bytes == payload
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_backend
        settings.workflow.temporal_artifact_root = original_root


def test_no_reintroduction_guard_allows_skill_vite_provenance_manifests() -> None:
    """Skill/Vite/provenance manifests stay allowed after removal."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "manifest_qualification_4193_guard",
        REPO_ROOT
        / "tests"
        / "unit"
        / "config"
        / "test_manifest_retirement_qualification_4193.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (
        mod.check_user_manifest_allowed(
            [
                "skill:my-skill/SKILL.md",
                ".vite/manifest.json",
                "provenance attestation manifest",
                "upload:manifest.yaml (generic artifact upload)",
                "saved-work checkpoint manifest",
            ]
        )
        == []
    )
    assert mod.check_repo_native_manifest_product_absent() == []


# ---------------------------------------------------------------------------
# R3: historical reads for both original input meanings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_compile_and_node_reads_decode_without_replay(
    tmp_path: Path,
) -> None:
    """Old compile + node histories decode; neither replays on the new binary."""
    from api_service.api.routers.executions import (
        _degraded_step_execution_projection_payload,
        _resolve_execution_entry,
        _serialize_execution,
        _serialize_execution_list_item,
        _step_execution_manifest_refs,
    )
    from api_service.db.models import TemporalExecutionCanonicalRecord
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )

    async with _temporal_db(tmp_path) as session:
        record = await _insert_historical_manifest_record(session)
        stored = await session.get(
            TemporalExecutionCanonicalRecord, record.workflow_id
        )
        assert stored is not None
        # Compile-history meaning: stored manifest_ref + manifest entry decode.
        assert stored.manifest_ref == "artifact://manifest/historical"
        assert _resolve_execution_entry(stored, {}) == "manifest"
        # Served read path: the same persisted record serializes through the
        # production list/detail projections used by GET /api/executions.
        serialized = _serialize_execution(stored)
        assert serialized.workflow_id == stored.workflow_id
        assert serialized.workflow_type == str(stored.workflow_type.value)
        list_item = _serialize_execution_list_item(stored)
        assert list_item.workflow_id == stored.workflow_id

        # Node-history meaning: manifestArtifactRef refs decode from a
        # ledger row double; degraded metadata keeps its own position.
        row = SimpleNamespace(
            refs=SimpleNamespace(
                step_execution_manifest_refs=["artifact://manifest/node-1"],
                latest_step_execution_manifest_ref="artifact://manifest/node-2",
            ),
            artifacts=None,
        )
        assert _step_execution_manifest_refs(row) == [
            "artifact://manifest/node-1",
            "artifact://manifest/node-2",
        ]
        ledger = SimpleNamespace(workflow_id="mm:w", run_id="run-1")
        degraded = _degraded_step_execution_projection_payload(
            ledger=ledger,  # type: ignore[arg-type]
            logical_step_id="step-1",
            manifest_artifact_ref="artifact://manifest/node-1",
            compatibility_decision={"decision": "incompatible"},
            fallback_ordinal=3,
        )
        assert degraded["manifestArtifactRef"] == "artifact://manifest/node-1"
        assert degraded["executionOrdinal"] == 3

        # Readable but not replayable: a rerun from the retired row rejects
        # before any launch side effect.
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock()  # type: ignore[attr-defined]
        with pytest.raises(TemporalExecutionValidationError, match="was retired"):
            await service._create_fresh_rerun_execution(
                stored,  # type: ignore[arg-type]
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                idempotency_key=None,
            )
        service._client_adapter.start_workflow.assert_not_awaited()  # type: ignore[attr-defined]


def test_retired_type_has_no_product_projection() -> None:
    """Product views exclude the retired type; history stays out of new work."""
    from moonmind.workflows.temporal.workflow_registry import (
        WorkflowProjectionExcluded,
        product_workflow_types,
        require_product_projection,
    )

    assert "MoonMind.ManifestIngest" not in product_workflow_types()
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.ManifestIngest")
    require_product_projection("MoonMind.UserWorkflow")


# ---------------------------------------------------------------------------
# R5: existing impact selection routes these boundaries to required CI
# ---------------------------------------------------------------------------


def test_impact_selection_routes_retirement_boundaries_to_required_ci() -> None:
    """Changed retirement files stay selected in existing CI."""
    from tools.select_test_suites import select_suites

    # This change touches only these two test paths; selection must be
    # computed from the real changed list, not injected production paths.
    changed = [
        "tests/unit/api/routers/test_manifest_retirement_boundaries_4193.py",
        "tests/unit/config/test_manifest_retirement_qualification_4193.py",
    ]
    boundary_selection = select_suites(changed)
    assert boundary_selection.unit_fast is True
    assert boundary_selection.api_component is True
    # These hermetic boundary tests do not by themselves select the
    # Temporal/integration lanes; those lanes are owned by the existing
    # production-path suites that cover the same boundaries.
    assert boundary_selection.temporal_boundary is False
    assert boundary_selection.integration_ci is False

    # Empty input selects the full gate: missing execution cannot pass as
    # a successful inventory check.
    assert select_suites([]).unit_fast is True
    assert select_suites([]).full_backend is True


# ---------------------------------------------------------------------------
# R6: representative normal workflow without Manifest/vector setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_workflow_launches_without_manifest_or_vector(
    tmp_path: Path,
) -> None:
    """An ordinary UserWorkflow launches with no Manifest/vector residue."""
    from moonmind.workflows.executions.execution_contract import (
        reject_retired_vector_fields,
        strip_absent_vector_fields,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    normal = {"instructions": "summarize this repo", "context": "hello"}
    assert strip_absent_vector_fields(dict(normal)) == normal
    residue = {"instructions": "summarize", "rag": {}}
    assert strip_absent_vector_fields(dict(residue)) == {"instructions": "summarize"}
    with pytest.raises(Exception, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"]}}, field_path="payload"
        )

    async with _temporal_db(tmp_path) as session:
        from api_service.db.models import TemporalExecutionCanonicalRecord

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
        # Runtime result: the launched execution persists and stays readable
        # with ordinary parameters and no Manifest/vector residue.
        stored = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert stored is not None
        assert str(stored.workflow_type.value) == "MoonMind.UserWorkflow"
        assert stored.manifest_ref is None
        parameters = dict(stored.parameters or {})
        assert "rag" not in parameters
        assert "manifestArtifactRef" not in parameters


def test_supported_agent_request_needs_no_manifest_or_vector() -> None:
    """A supported runtime request validates without Manifest/vector settings."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-1",
        correlation_id="corr-4193-1",
        idempotency_key="idem-4193-1",
    )
    dumped = request.model_dump(by_alias=True)
    assert dumped["agentKind"] == "managed"
    parameters = dumped.get("parameters", {})
    assert "rag" not in parameters
    assert "followUpRetrieval" not in parameters
