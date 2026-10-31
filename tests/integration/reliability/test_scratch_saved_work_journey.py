"""Scratch work survives host loss and separately admitted publication retries.

MoonLadderStudios/MoonMind#4003 (outcome 1, owned by #2615). A scratch task
compiles through the real admission writer, prepares its sandbox through the
production generic-host composition, saves the generated output through the
generic realizer's own finalization and cleanup owners into the production
artifact service, and is downloaded through the authorized artifact endpoint
after the worker workspace is deleted. Publication then uses the existing
saved-work journey with a local bare destination and a controlled GitHub provider.
Docker, the agent process and Temporal dispatch are substituted. Scratch does not
resolve repository credentials; later publication admits destination B over
ambient A and reconciles a lost push acknowledgment without repeating compute.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.api.routers import executions
from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
)
from api_service.api.routers.temporal_artifacts import router as artifact_router
from api_service.auth_providers import get_artifact_access
from api_service.db import base
from api_service.db.models import Base
from api_service.services import omnigent_execution_plan_service as writer
from api_service.services.omnigent_execution_plan_service import persist_json_artifact
from moonmind.auth import bound_acquisition, github_credentials
from moonmind.omnigent import production
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import WorkflowStartResult
from moonmind.workflows.temporal.runtime import managed_api_key_resolve
from tests.artifact_access import access_for_test
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.integration.reliability.test_repository_authority_handoffs_4009 import (
    _WORKFLOW,
    _compile,
)
from tests.support.saved_work_capture import CapturedSavedWork
from tests.unit.api.routers.test_executions import _saved_work_app
from tests.unit.publish.test_saved_work_publication_journey import (
    REPOSITORY,
    _tree,
    _WorkerLost,
    journey,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _ready_opencode_image_pair,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_STEP = f"{_WORKFLOW}:run-1:implement:execution:1"
_WORKSPACE_ID = hashlib.sha256(f"{_WORKFLOW}:{_STEP}".encode()).hexdigest()[:24]
_GENERATED = b"# Generated report\n\nNo repository was needed.\n"


@pytest_asyncio.fixture
async def authority_context(tmp_path, monkeypatch):
    """Real plan/blob authority, retaining competing ambient repository setup."""
    monkeypatch.setenv("SELECTED_REPOSITORY_PAT", "selected-secret-canary")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret-canary")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection("selected-repository", "SELECTED_REPOSITORY_PAT"),
        assignments=[
            github_repository_assignment(
                "selected-repository", "MoonLadderStudios/MoonMind"
            )
        ],
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", sessions)
    monkeypatch.setattr(
        writer, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        writer,
        "resolve_execution_evidence",
        lambda *_args, **_kwargs: (None, "uncertified"),
    )
    try:
        yield sessions, _OnDemandTemporalArtifactService(sessions), None
    finally:
        await production.close_omnigent_transport_pool()
        await engine.dispose()


def _forbid_repository_credentials(monkeypatch) -> list[str]:
    """Fail the journey if any repository credential owner is consulted."""

    calls: list[str] = []

    def forbid(owner, name):
        async def refuse(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"scratch work consulted {name}")

        monkeypatch.setattr(owner, name, refuse)

    for name in (
        "admitted_repository_identity",
        "acquire_repository_use",
        "_current_repository_connection",
        "_verified_repository_access",
    ):
        forbid(OmnigentGithubCredentialService, name)
    forbid(bound_acquisition.BoundCredentialAcquirer, "acquire")
    for name in (
        "resolve_github_credential",
        "resolve_deployment_github_credential",
        "resolve_connection_github_credential",
    ):
        forbid(github_credentials, name)
    forbid(managed_api_key_resolve, "resolve_github_token_for_launch")
    return calls


async def _serialize_finalization_on_sqlite(sessions) -> None:
    """Give the file-backed SQLite engine PostgreSQL's finalization lock name.

    One process and SQLite's own write serialization stand in for the
    transaction-scoped advisory lock; the binding store's revision/fence CAS
    stays real.
    """

    engine = sessions.kw["bind"]

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_connection, _record):
        dbapi_connection.create_function("pg_try_advisory_xact_lock", 1, lambda _key: 1)

    await engine.dispose()


async def test_scratch_output_is_saved_and_downloaded_after_host_loss(
    authority_context, monkeypatch, tmp_path, request
):
    sessions, _plan_artifacts, _plan_reader = authority_context
    await _serialize_finalization_on_sqlite(sessions)
    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    blob_root = tmp_path / "durable-artifacts"
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(blob_root)),
    )

    # Admission: no repository source, so only model authority is bound.
    operator = str(uuid.UUID(int=4003))
    task = {"instructions": "Generate a report without a repository."}
    snapshot = executions._build_original_workflow_input_snapshot_payload(
        source_kind="create", payload={}, task_payload=task
    )
    async with sessions() as session:
        original_inputs = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(blob_root),
        )
        task_input_ref, task_input_digest = await persist_json_artifact(
            artifact_service=original_inputs,
            principal=operator,
            artifact_class="original_task_input_snapshot",
            payload=snapshot,
        )
    compiled = await _compile(
        monkeypatch,
        authority_context,
        workflow=task,
        task_input_snapshot_ref=task_input_ref,
        task_input_snapshot_digest=task_input_digest,
    )
    plan = await reader._load_verified_execution_plan(
        compiled.binding,
        workflow_id=_WORKFLOW,
        run_id="run-1",
        access=access_for_test("user-1"),
    )
    assert set(plan.payload.credentialBindings) == {"primary-model"}
    assert not plan.payload.resolvedTools.get("repositoryAccess")
    real_access = managed_api_key_resolve.select_github_access_for_launch
    real_ambient = github_credentials.resolve_github_credential
    credential_patch = pytest.MonkeyPatch()
    request.addfinalizer(credential_patch.undo)
    forbidden = _forbid_repository_credentials(credential_patch)

    daemon_calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **kwargs):
            daemon_calls.append(list(argv))
            return 0, "", ""

    async def daemon_runner(argv, input_bytes=None):
        daemon_calls.append(list(argv))
        assert not input_bytes, "scratch preparation must not deliver a secret"
        return 0, "", ""

    monkeypatch.setattr(production, "DockerCommandBackend", Backend)
    monkeypatch.setattr(production, "generic_host_enabled", lambda: True)
    monkeypatch.setattr(
        production, "resolved_server_url", lambda: "http://omnigent:8080"
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "http://omnigent:8080")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER", "deployment")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(worker_root))
    gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
    services = production.build_generic_omnigent_execution_services(
        session_factory=sessions, artifact_gateway=gateway
    )
    realizer = services.generic_realizer
    runtime = realizer._host_runtime
    runtime._workspace._runner = daemon_runner
    runtime._skills = SimpleNamespace(
        anticipated_attachment=AsyncMock(return_value={"kind": "bind"}),
        materialize=AsyncMock(return_value={"kind": "bind"}),
        cleanup=AsyncMock(),
    )
    runtime._tools = SimpleNamespace(materialize=AsyncMock(return_value=()))
    runtime._egress = SimpleNamespace(attest=AsyncMock(return_value={}))

    plan_binding = compiled.binding.model_dump(by_alias=True, mode="json")
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "provider-opencode-native",
            "correlationId": _WORKFLOW,
            "idempotencyKey": "scratch-attempt",
            "omnigentExecutionPlan": plan_binding,
            "workspaceSpec": {
                "workspaceSource": {"kind": "scratch"},
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": _WORKSPACE_ID,
                    "relativePath": "repo",
                },
            },
            "stepExecution": {
                "workflowId": _WORKFLOW,
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": _STEP,
                "runtimeContextPolicy": "fresh_agent_run",
                "omnigentExecutionPlan": plan_binding,
            },
        }
    )
    store = DbRuntimeBindingStore(sessions)
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    owner = f"{binding.bindingId}:{binding.fencingGeneration}"
    prepared = await runtime.prepare(
        request=request,
        plan=plan,
        host_class=SimpleNamespace(
            runtime={"uid": os.getuid(), "gid": os.getgid()},
            imageRef="host-image",
            omnigentVersion="",
        ),
        launch_policy=SimpleNamespace(),
        repository_owner_ref=owner,
    )
    # No clone, no Git/gh projection: an empty attempt-owned directory.
    assert prepared.github_credential_attachment is None
    assert not any("-ceu" in argv for argv in daemon_calls)
    workspace = worker_root / "temporal_sandbox" / _WORKSPACE_ID / "repo"
    assert workspace.is_dir() and not any(workspace.iterdir())

    # The substituted agent process writes generated output.
    (workspace / "report.md").write_bytes(_GENERATED)

    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        request.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
            include={
                "agent_kind",
                "agent_id",
                "correlation_id",
                "idempotency_key",
                "step_execution",
            },
        )
        | {"workspaceSpec": dict(request.workspace_spec)},
    )
    computed = AgentRunResult(summary="report generated")
    await sink.record_phase(
        "compute", computed.model_dump(by_alias=True, mode="json", exclude_none=True)
    )
    finished = await realizer._finish_execution(request, sink, computed)
    assert finished.failure_class is None
    assert finished.metadata["workPreserved"] is True
    cleaned, _host = await realizer._cleanup(
        request=request,
        binding=await store.get(binding.bindingId),
        host_lease=None,
        host_context=None,
        prepared=prepared,
        credential_handles=(),
        acquired=(),
    )
    assert cleaned.state is RuntimeBindingState.cleaned
    saved = cleaned.phaseResults["saved"]
    assert saved == finished.metadata["savedWorkspaceCheckpoint"]
    assert saved[
        "savedWorkRef"
    ], "generic saved output must be publishable without compute"
    assert saved["savedWorkDigest"].startswith("sha256:")

    archive_id = saved["archiveRef"].removeprefix("artifact://")
    async with sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(blob_root),
        )

        async def captured(_service):
            assert _service is service
            return CapturedSavedWork(
                saved_work_ref=saved["savedWorkRef"],
                saved_work_digest=saved["savedWorkDigest"],
                baseline_commit="",
                source_repo=worker_root,
                workflow_id=_WORKFLOW,
                run_id="run-1",
            )

        # Reuse the existing publication journey, including its source deletion
        # and fail-if-used agent/restore owners. Only the provider transport and
        # Temporal Activity dispatcher are substituted here.
        assert forbidden == []
        credential_patch.undo()
        async with journey(
            tmp_path,
            monkeypatch,
            artifact_service=service,
            saved_work_factory=captured,
            destination_files={"README.md": "destination only\n"},
        ) as state:
            assert not worker_root.exists()
            state.artifacts_root = blob_root
            artifact, _links, _pinned, _policy = await service.get_metadata(
                artifact_id=saved["savedWorkRef"]
                .removeprefix("artifact://")
                .removeprefix("artifact:"),
                access=access_for_test(operator),
            )
            summary = artifact.metadata_json["saved_work_summary"]
            assert summary["outputs"][0]["artifact_id"] == archive_id
            assert summary["outputs"][0]["status"] == "self_contained"
            app = FastAPI()
            app.include_router(artifact_router)
            app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
            app.dependency_overrides[get_artifact_access] = lambda: access_for_test(
                operator
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                app.dependency_overrides[get_artifact_access] = lambda: access_for_test(
                    "workflow:mm:unrelated"
                )
                denied = await client.get(f"/api/artifacts/{archive_id}/download")
                assert denied.status_code in {403, 404}, denied.text
                app.dependency_overrides[get_artifact_access] = lambda: access_for_test(
                    operator
                )
                response = await client.get(f"/api/artifacts/{archive_id}/download")
            assert response.status_code == 200, response.text
            assert (
                "sha256:" + hashlib.sha256(response.content).hexdigest()
                == saved["archiveDigest"]
            )
            with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
                member = archive.extractfile("report.md")
                assert member is not None and member.read() == _GENERATED

            # Publication is separately admitted using the newly selected
            # destination connection B, never ambient A or source credentials.
            monkeypatch.setattr(
                managed_api_key_resolve, "select_github_access_for_launch", real_access
            )
            monkeypatch.setattr(
                github_credentials, "resolve_github_credential", real_ambient
            )
            monkeypatch.setenv("DESTINATION_PAT_B", "destination-b-fixture")
            connection = github_pat_connection(
                DEFAULT_GIT_CONNECTION_REF, "DESTINATION_PAT_B"
            )
            (tmp_path / "destination-authority").mkdir()
            destination_engine = await record_repository_connections(
                monkeypatch,
                tmp_path / "destination-authority",
                connection,
                assignments=(
                    github_repository_assignment(
                        DEFAULT_GIT_CONNECTION_REF, REPOSITORY
                    ),
                ),
            )
            try:
                app, adapter, record, user = _saved_work_app(
                    monkeypatch, service, connection
                )
                record.namespace = "default"
                record.workflow_id = _WORKFLOW
                record.run_id = "run-1"
                record.search_attributes["mm_owner_id"] = str(user.id)
                monkeypatch.setattr(
                    executions, "_execution_principal", lambda _user: operator
                )
                adapter.start_workflow.side_effect = (
                    lambda **kwargs: WorkflowStartResult(
                        workflow_id=kwargs["workflow_id"], run_id="publication-run"
                    )
                )
                body = {
                    "savedWorkRef": saved["savedWorkRef"],
                    "sourceRunId": "run-1",
                    "destination": {
                        "repository": REPOSITORY,
                        "objective": "pr",
                        "baseBranch": "main",
                        "headBranch": "saved/work",
                        "strategy": "additive_import",
                    },
                }
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    admitted = await client.post(
                        f"/api/executions/{_WORKFLOW}/retry-publication", json=body
                    )
                assert admitted.status_code == 201, admitted.text
                contract = adapter.start_workflow.await_args.kwargs["input_args"]
                assert contract["savedWorkDigest"] == saved["savedWorkDigest"]
                assert task["instructions"] in contract["pullRequestBody"]
                assert task_input_ref in contract["pullRequestBody"]
                before = state.saved_bytes()
                assert before, "the original saved bytes must be checked after retry"

                async def lose_push_ack(attempt):
                    if attempt == 1:
                        raise _WorkerLost()

                state.after["publication_recovery.saved_work_push"] = lose_push_ack
                result = await state.run(contract)
                assert result["outcome"] == "published"
                assert result["implementationRerun"] is False
                assert result["push"]["status"] == "reconciled"
                assert _tree(state.remote, "refs/heads/saved/work") == {
                    "README.md": "destination only",
                    "report.md": _GENERATED.decode().strip(),
                }
                assert len(state.pushes()) == len(state.provider.creates) == 1
                assert set(state.provider.tokens) == {"destination-b-fixture"}
                assert (
                    state.provider.creates[0]["github_token"] == "destination-b-fixture"
                )
                assert task["instructions"] in state.provider.creates[0]["body"]
                assert state.saved_objects_unchanged(before)
                assert await state.use_claims() == []
                again = await state.run(contract)
                assert again["outcome"] == "published"
                assert again["candidate"] == result["candidate"]
                assert len(state.pushes()) == len(state.provider.creates) == 1
                assert state.saved_objects_unchanged(before)
                assert all(
                    name.startswith("publication_recovery.") for name in state.calls
                )
            finally:
                await destination_engine.dispose()
