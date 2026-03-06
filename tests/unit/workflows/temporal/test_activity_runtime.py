"""Unit tests for Temporal activity-family runtime helpers."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.jules_models import JulesTaskResponse
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_FLEET,
    SANDBOX_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalJulesActivities,
    TemporalPlanActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    build_activity_bindings,
    build_activity_execution_context,
    build_activity_invocation_envelope,
    build_compact_activity_result,
    build_observability_summary,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_activity_runtime.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()


def _registry_payload() -> dict:
    return {
        "skills": [
            {
                "name": "repo.run_tests",
                "version": "1.0.0",
                "description": "Run tests",
                "inputs": {
                    "schema": {
                        "type": "object",
                        "required": ["repo_ref"],
                        "properties": {"repo_ref": {"type": "string"}},
                    }
                },
                "outputs": {
                    "schema": {
                        "type": "object",
                        "required": ["ok"],
                        "properties": {"ok": {"type": "boolean"}},
                    }
                },
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {"max_attempts": 2},
                },
            }
        ]
    }


def _plan_payload(*, registry_artifact_id: str, registry_digest: str) -> dict:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Fix tests",
            "created_at": "2026-03-05T00:00:00Z",
            "registry_snapshot": {
                "digest": registry_digest,
                "artifact_ref": registry_artifact_id,
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "n1",
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                "inputs": {"repo_ref": "git:org/repo#main"},
            }
        ],
        "edges": [],
    }


class _FakeJulesClient:
    def __init__(
        self,
        *,
        create_status: str = "pending",
        get_status: str = "completed",
    ) -> None:
        self.created: list[object] = []
        self.lookups: list[object] = []
        self.closed = False
        self._create_status = create_status
        self._get_status = get_status

    async def create_task(self, request):
        self.created.append(request)
        return JulesTaskResponse(
            taskId="task-001",
            status=self._create_status,
            url="https://jules.test/task-001",
        )

    async def get_task(self, request):
        self.lookups.append(request)
        return JulesTaskResponse(
            taskId=request.task_id,
            status=self._get_status,
            url="https://jules.test/task-001",
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_artifact_activity_create_returns_ref_and_upload_descriptor(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            artifact_ref, upload = await activities.artifact_create(
                principal="user-1",
                content_type="text/plain",
            )

            assert artifact_ref.artifact_id.startswith("art_")
            assert upload.mode == "single_put"


async def test_plan_validate_accepts_temporal_registry_artifact_ids(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_ref = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            registry_artifact, _upload = registry_ref
            registry_completed = await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            planner = TemporalPlanActivities(artifact_service=service)
            registry_payload = _registry_payload()

            snapshot = create_registry_snapshot(
                skills=parse_skill_registry(registry_payload),
                artifact_store=InMemoryArtifactStore(),
            )
            plan_payload = _plan_payload(
                registry_artifact_id=registry_completed.artifact_id,
                registry_digest=snapshot.digest,
            )
            plan_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=plan_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(plan_payload) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            validated_ref = await planner.plan_validate(
                plan_ref=plan_artifact.artifact_id,
                registry_snapshot_ref=registry_completed.artifact_id,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=validated_ref.artifact_id,
                principal="user-1",
            )

            assert b'"plan_version": "1.0"' in payload


async def test_skill_execute_loads_registry_snapshot_from_temporal_artifact(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                version="1.0.0",
                handler=lambda inputs, _context: SkillResult(
                    status="SUCCEEDED",
                    outputs={"ok": inputs["repo_ref"].endswith("#main")},
                    progress={"percent": 100},
                ),
            )

            activities = TemporalSkillActivities(dispatcher=dispatcher)
            result = await activities.mm_skill_execute(
                invocation_payload={
                    "id": "n1",
                    "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                    "inputs": {"repo_ref": "git:org/repo#main"},
                },
                registry_snapshot_ref=registry_artifact.artifact_id,
                artifact_service=service,
                principal="user-1",
            )

            assert result.status == "SUCCEEDED"
            assert result.outputs["ok"] is True


async def test_sandbox_run_command_writes_diagnostics_artifact(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            workspace_root = tmp_path / "workspaces"
            workspace = workspace_root / "temporal_sandbox" / "unit-run-command"
            workspace.mkdir(parents=True)
            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=workspace_root,
            )

            result = await activities.sandbox_run_command(
                workspace_ref=workspace,
                cmd="printf 'hello sandbox'",
                principal="user-1",
                execution_ref=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="output.logs",
                ),
            )
            assert result.exit_code == 0
            assert result.diagnostics_ref is not None

            _artifact, payload = await service.read(
                artifact_id=result.diagnostics_ref.artifact_id,
                principal="user-1",
            )
            assert b"hello sandbox" in payload


async def test_sandbox_rejects_workspace_outside_sandbox_root(tmp_path: Path):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    outside_workspace = tmp_path / "outside"
    outside_workspace.mkdir()

    with pytest.raises(TemporalActivityRuntimeError, match="escapes sandbox root"):
        await activities.sandbox_run_command(
            workspace_ref=outside_workspace,
            cmd=("pwd",),
        )


async def test_sandbox_checkout_rejects_local_path_outside_workspace_root(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    source = tmp_path / "repo"
    source.mkdir()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="must be under workspace_root",
    ):
        await activities.sandbox_checkout_repo(
            repo_ref=source,
            idempotency_key="checkout-outside",
        )


async def test_shared_envelope_helpers_build_compact_runtime_contracts():
    invocation = build_activity_invocation_envelope(
        correlation_id="corr-1",
        idempotency_key="idem-1",
        input_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V8"],
        parameters={"phase": "run"},
    )
    result = build_compact_activity_result(
        output_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"],
        summary={"status": "ok"},
        metrics={"tokens": 12},
        diagnostics_ref="art_01HJ4M3Y7RM4C5S2P3Q8G6T7VA",
    )
    context = build_activity_execution_context(
        workflow_id="wf-1",
        run_id="run-1",
        activity_id="act-1",
        attempt=2,
        task_queue="mm.activity.sandbox",
    )
    summary = build_observability_summary(
        context=context,
        activity_type="sandbox.run_command",
        correlation_id=invocation.correlation_id,
        idempotency_key="idem-1",
        outcome="succeeded",
        diagnostics_ref=result.diagnostics_ref,
        metrics_dimensions={"fleet": "sandbox"},
    )

    assert invocation.to_payload()["idempotency_key"] == "idem-1"
    assert result.to_payload()["output_refs"] == ["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"]
    assert summary.activity_type == "sandbox.run_command"
    assert summary.idempotency_key_hash != "idem-1"


async def test_sandbox_checkout_apply_patch_and_run_tests(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            repo = tmp_path / "workspaces" / "repo"
            repo.mkdir(parents=True)
            (repo / "sample.txt").write_text("hello\n", encoding="utf-8")
            (repo / "tools").mkdir()
            (repo / "tools" / "test_unit.sh").write_text(
                "#!/usr/bin/env sh\nprintf 'tests ok'\n",
                encoding="utf-8",
            )
            (repo / "tools" / "test_unit.sh").chmod(0o755)

            patch_artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )
            await service.write_complete(
                artifact_id=patch_artifact.artifact_id,
                principal="user-1",
                payload=(
                    "--- sample.txt\n"
                    "+++ sample.txt\n"
                    "@@ -1 +1 @@\n"
                    "-hello\n"
                    "+patched\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path / "workspaces",
            )
            workspace = await activities.sandbox_checkout_repo(
                repo_ref=repo,
                idempotency_key="checkout-1",
            )
            assert Path(workspace).exists()

            patched_workspace = await activities.sandbox_apply_patch(
                workspace_ref=workspace,
                patch_ref=patch_artifact.artifact_id,
                principal="user-1",
            )
            assert (
                Path(patched_workspace, "sample.txt").read_text(encoding="utf-8")
                == "patched\n"
            )

            report_ref = await activities.sandbox_run_tests(
                workspace_ref=patched_workspace,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=report_ref.artifact_id,
                principal="user-1",
            )
            assert b'"exit_code": 0' in payload


async def test_jules_activities_persist_tracking_artifacts(tmp_path: Path):
    fake_client = _FakeJulesClient()

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalJulesActivities(
                artifact_service=service,
                client_factory=lambda: fake_client,
            )

            started = await activities.integration_jules_start(
                principal="user-1",
                parameters={"title": "Test task", "description": "run tests"},
                execution_ref=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="output.summary",
                ),
            )
            assert started.external_id == "task-001"
            assert started.tracking_ref is not None
            assert started.provider_status == "pending"
            assert started.normalized_status == "queued"
            assert started.callback_supported is False
            assert started.external_url == "https://jules.test/task-001"

            status = await activities.integration_jules_status(
                external_id="task-001",
                principal="user-1",
            )
            assert status.status == "completed"
            assert status.provider_status == "completed"
            assert status.normalized_status == "succeeded"
            assert status.terminal is True

            fetched = await activities.integration_jules_fetch_result(
                external_id="task-001",
                principal="user-1",
            )
            assert len(fetched) == 1
            assert fetched[0].artifact_id.startswith("art_")
            assert fake_client.closed is True


async def test_jules_start_reuses_external_identity_for_same_idempotency_key(
    tmp_path: Path,
):
    fake_client = _FakeJulesClient()

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalJulesActivities(
                artifact_service=service,
                client_factory=lambda: fake_client,
            )

            first = await activities.integration_jules_start(
                principal="user-1",
                parameters={"title": "same", "description": "same"},
                idempotency_key="idem-jules-1",
            )
            second = await activities.integration_jules_start(
                principal="user-1",
                parameters={"title": "same", "description": "same"},
                idempotency_key="idem-jules-1",
            )

            assert first.external_id == second.external_id
            assert len(fake_client.created) == 1
            assert fake_client.created[0].metadata["idempotencyKey"] == "idem-jules-1"


async def test_jules_start_requires_description_or_inputs_ref(tmp_path: Path):
    fake_client = _FakeJulesClient()

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalJulesActivities(
                artifact_service=service,
                client_factory=lambda: fake_client,
            )

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="requires parameters.description or inputs_ref",
            ):
                await activities.integration_jules_start(
                    principal="user-1",
                    parameters={"title": "missing description"},
                )

            assert fake_client.created == []


async def test_jules_start_embeds_correlation_metadata(tmp_path: Path):
    fake_client = _FakeJulesClient()

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalJulesActivities(
                artifact_service=service,
                client_factory=lambda: fake_client,
            )

            await activities.integration_jules_start(
                principal="user-1",
                correlation_id="corr-jules-1",
                parameters={"title": "with correlation", "description": "verify"},
            )

            assert fake_client.created[0].metadata["correlationId"] == "corr-jules-1"


async def test_jules_fetch_result_writes_failure_summary_artifact(tmp_path: Path):
    fake_client = _FakeJulesClient(get_status="failed")

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalJulesActivities(
                artifact_service=service,
                client_factory=lambda: fake_client,
            )

            fetched = await activities.integration_jules_fetch_result(
                external_id="task-001",
                principal="user-1",
                execution_ref=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="output.summary",
                ),
            )

            assert len(fetched) == 2
            _artifact, summary_payload = await service.read(
                artifact_id=fetched[1].artifact_id,
                principal="user-1",
            )
            summary = json.loads(summary_payload.decode("utf-8"))
            assert summary["providerStatus"] == "failed"
            assert summary["normalizedStatus"] == "failed"
            assert summary["externalId"] == "task-001"


async def test_default_jules_client_uses_shared_runtime_gate_message(monkeypatch):
    monkeypatch.delenv("JULES_ENABLED", raising=False)
    monkeypatch.delenv("JULES_API_URL", raising=False)
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)

    activities = TemporalJulesActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match=JULES_RUNTIME_DISABLED_MESSAGE,
    ):
        await activities.integration_jules_status(external_id="task-001")


async def test_build_activity_bindings_filters_to_requested_fleet(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                plan_activities=TemporalPlanActivities(artifact_service=service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=SkillActivityDispatcher()
                ),
                sandbox_activities=TemporalSandboxActivities(artifact_service=service),
                integration_activities=TemporalJulesActivities(
                    artifact_service=service,
                    client_factory=_FakeJulesClient,
                ),
                fleets=(ARTIFACTS_FLEET,),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {ARTIFACTS_FLEET}
            assert "mm.skill.execute" in {binding.activity_type for binding in bindings}
            assert "artifact.lifecycle_sweep" in {
                binding.activity_type for binding in bindings
            }
            assert any(
                binding.handler.__name__ == "artifact_lifecycle_sweep"
                for binding in bindings
            )


async def test_build_activity_bindings_requires_selected_family_implementation(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="sandbox implementation",
            ):
                build_activity_bindings(
                    catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    fleets=(SANDBOX_FLEET,),
                )
