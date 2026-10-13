"""Scratch work is admitted, saved and downloadable after its host is gone.

MoonLadderStudios/MoonMind#4003 (outcome 1, owned by #2615). A scratch task
compiles through the real admission writer, prepares its sandbox through the
production generic-host composition, saves the generated output through the
generic realizer's own finalization and cleanup owners into the production
artifact service, and is downloaded through the authorized artifact endpoint
after the worker workspace is deleted. Only the Docker daemon and the agent
process are substituted. The deployment holds a selected connection and an
ambient token throughout; neither is resolved, acquired or delivered.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    _resolve_principal,
)
from api_service.api.routers.temporal_artifacts import router as artifact_router
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
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime import managed_api_key_resolve
from tests.integration.reliability.test_repository_authority_handoffs_4009 import (  # noqa: F401
    _WORKFLOW,
    _compile,
    authority_context,
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
        dbapi_connection.create_function(
            "pg_try_advisory_xact_lock", 1, lambda _key: 1
        )

    await engine.dispose()


async def test_scratch_output_is_saved_and_downloaded_after_host_loss(
    authority_context, monkeypatch, tmp_path
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
    compiled = await _compile(monkeypatch, authority_context)
    plan = await reader._load_verified_execution_plan(compiled.binding)
    assert set(plan.payload.credentialBindings) == {"primary-model"}
    assert not plan.payload.resolvedTools.get("repositoryAccess")
    forbidden = _forbid_repository_credentials(monkeypatch)

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
            runtime={"uid": 0, "gid": 0}, imageRef="host-image", omnigentVersion=""
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

    # The host, its workspace and the worker's local state are gone.
    shutil.rmtree(worker_root)

    archive_id = saved["archiveRef"].removeprefix("artifact://")
    operator = str(uuid.UUID(int=4003))
    async with sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(blob_root),
        )
        app = FastAPI()
        app.include_router(artifact_router)
        app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
        app.dependency_overrides[_resolve_principal] = lambda: operator
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/artifacts/{archive_id}/download")
    assert response.status_code == 200, response.text
    assert "sha256:" + hashlib.sha256(response.content).hexdigest() == saved[
        "archiveDigest"
    ]
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        member = archive.extractfile("report.md")
        assert member is not None and member.read() == _GENERATED
    assert forbidden == []
