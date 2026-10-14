"""A live Omnigent host's agent-side gh login outlives one App issuance (#4011).

The generic realizer re-issues the attempt-owned ``hosts.yml`` through the
same frozen collaboration binding before an expiring GitHub App token lapses.
A refused re-issue (revoked or changed connection) writes nothing, so the
running ``gh`` never receives another account's or a lapsed credential.
Only Docker and the GitHub App provider are replaced; the plan compiler,
recorded connections, bound acquirer, runtime binding store, realizer and
projection protocol are production code.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.db import base
from api_service.db.models import Base, RepositoryConnectionRecord
from api_service.services import omnigent_execution_plan_service as writer
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.github_credentials import (
    github_projection_script,
)
from moonmind.omnigent.runtime_bindings import (
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
    reserve_github_projection,
    validate_github_projection,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from tests.helpers.repository_connections import (
    github_app_connection,
    github_app_provider_edge,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _ready_opencode_image_pair,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _compile_opencode_plan,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_REPO = "MoonLadderStudios/MoonMind"
_WORKFLOW = "mm:test-gh-projection-refresh"
_CONNECTION = "selected-repository"
_OPERATIONS = ("read", "write", "branch_write", "review_request", "merge_request")
_ACTIONS = ("reserve", "publish", "inspect", "retire", "retire_legacy")
#: An App installation token that lapses well inside the refresh lead.
_LIFETIME_SECONDS = 120


@pytest_asyncio.fixture
async def app_context(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret-canary")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    issued = github_app_provider_edge(
        monkeypatch,
        # The source clone and the collaboration projection each issue.
        ["ghs_source_clone", "ghs_first_issuance", "ghs_second_issuance", "ghs_unexpected"],
        repositories=(_REPO,),
        lifetime_seconds=_LIFETIME_SECONDS,
    )
    app = github_app_connection(_CONNECTION, _REPO)
    app = app.model_copy(
        update={
            "allowed_operations": _OPERATIONS,
            "credential": app.credential.model_copy(
                update={"permitted_repositories": (_REPO,)}
            ),
        }
    )
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        app,
        assignments=[
            github_repository_assignment(_CONNECTION, _REPO, operations=_OPERATIONS)
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
    artifacts = _ArtifactService()

    async def read_json(ref, **_kwargs):
        return json.loads(artifacts.payloads[ref.removeprefix("artifact:")])

    async def read_bytes(ref):
        return artifacts.payloads[ref.removeprefix("artifact:")]

    async def read_repository_access_snapshot(ref, *, request):
        return await read_bytes(ref)

    async def read(*, artifact_id, **_kwargs):
        return SimpleNamespace(artifact_id=artifact_id), artifacts.payloads[artifact_id]

    artifacts.read = read
    monkeypatch.setattr(reader, "_read_json_artifact", read_json)
    try:
        yield SimpleNamespace(
            sessions=sessions,
            artifacts=artifacts,
            issued=issued,
            gateway=SimpleNamespace(
                read_bytes=read_bytes,
                read_repository_access_snapshot=read_repository_access_snapshot,
            ),
        )
    finally:
        from moonmind.omnigent.production import close_omnigent_transport_pool

        await close_omnigent_transport_pool()
        await engine.dispose()


def _target():
    return {
        "provider": "git",
        "connectionRef": _CONNECTION,
        "repository": {"name": _REPO},
        "branch": {"name": "main"},
    }


class _ProjectionBackend:
    """Docker double that runs the production projection protocol locally."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.owner = ""
        self.writes: list[bytes] = []

    async def run(self, argv, **kwargs):
        if argv[1:3] == ["volume", "inspect"]:
            return 0, hashlib.sha256(self.owner.encode()).hexdigest()[:32], ""
        if argv[1:2] != ["run"] or "-ceu" not in argv:
            return 0, "", ""
        script = argv[argv.index("-ceu") + 1]
        action = next(
            (name for name in _ACTIONS if script == github_projection_script(action=name)),
            None,
        )
        if action is None:
            return 0, "", ""
        token = kwargs.get("input_bytes")
        if token:
            self.writes.append(token)
        host, stamp = argv[-2:]
        completed = subprocess.run(
            [
                "sh",
                "-ceu",
                github_projection_script(str(self.config_dir), action=action),
                "--",
                str(os.getuid()),
                str(os.getgid()),
                host,
                stamp,
            ],
            input=token or b"",
            capture_output=True,
        )
        if completed.returncode:
            raise HarnessPlatformError(
                "projection writer failed",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return 0, completed.stdout.decode().strip(), ""

    def projected(self) -> tuple[dict, str]:
        stamp_line, *rest = (self.config_dir / "hosts.yml").read_text().splitlines()
        token = next(
            line.split(": ", 1)[1] for line in rest if "oauth_token" in line
        )
        return json.loads(stamp_line.split(": ", 1)[1]), token


async def _prepared_live_host(monkeypatch, context, tmp_path):
    """Admit, compile and prepare one gh-enabled attempt on a generic host."""

    compiled = await _compile_opencode_plan(
        monkeypatch,
        artifacts=context.artifacts,
        launch_policy_ref="omnigent-on-demand@1",
        plan_store=DbExecutionPlanStore(context.sessions),
        session_factory=context.sessions,
        extra_parameters={"requiredCapabilities": ["gh"], "repository": _target()},
        workflow_id=_WORKFLOW,
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    backend = _ProjectionBackend(tmp_path / "gh-projection")

    async def clone_runner(argv, input_bytes=None):
        if "-ceu" in argv:
            checkout = tmp_path / Path(argv[-3].removeprefix("/work/"))
            (checkout / ".git").mkdir(parents=True)
            (checkout / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        return 0, "", ""

    from moonmind.omnigent import production

    monkeypatch.setattr(production, "DockerCommandBackend", lambda: backend)
    monkeypatch.setattr(production, "generic_host_enabled", lambda: True)
    monkeypatch.setattr(
        production, "resolved_server_url", lambda: "http://omnigent:8080"
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "http://omnigent:8080")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER", "deployment")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    services = production.build_generic_omnigent_execution_services(
        session_factory=context.sessions, artifact_gateway=context.gateway
    )
    realizer = services.generic_realizer
    runtime = realizer._host_runtime
    runtime._workspace._runner = clone_runner
    skill_attachment = {"kind": "bind", "sourceRef": "skills", "cleanupRef": "skills"}
    runtime._skills = SimpleNamespace(
        anticipated_attachment=AsyncMock(return_value=skill_attachment),
        materialize=AsyncMock(return_value=skill_attachment),
        cleanup=AsyncMock(),
    )
    runtime._tools = SimpleNamespace(materialize=AsyncMock(return_value=()))
    runtime._egress = SimpleNamespace(attest=AsyncMock(return_value={}))
    workspace_id = hashlib.sha256(f"{_WORKFLOW}:attempt-4011".encode()).hexdigest()[
        :24
    ]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-opencode-native",
        correlationId=_WORKFLOW,
        idempotencyKey="attempt-4011",
        workspaceSpec={
            "repositoryTarget": _target(),
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
        },
    )
    store = realizer._runtime_bindings
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    for state in (
        RuntimeBindingState.credentials_acquired,
        RuntimeBindingState.credentials_materialized,
    ):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )
    binding, reservation = await reserve_github_projection(store, binding)
    backend.owner = f"{binding.bindingId}:{binding.fencingGeneration}"
    sink = RuntimeBindingSessionAuthoritySink(store, binding)

    async def verify():
        await validate_github_projection(store, binding.bindingId, reservation)

    prepared = await runtime.prepare(
        request=request,
        plan=plan,
        host_class=SimpleNamespace(
            runtime={"uid": os.getuid(), "gid": os.getgid()},
            imageRef="host-image",
            omnigentVersion="",
        ),
        launch_policy=SimpleNamespace(),
        authority_sink=sink.record_authority,
        repository_owner_ref=backend.owner,
        github_projection_reservation=reservation,
        github_projection_verifier=verify,
    )
    renewal_settings = {
        "writerImageRef": "host-image",
        "expectedOmnigentVersion": "",
        "runtimeUid": os.getuid(),
        "runtimeGid": os.getgid(),
    }
    return realizer, request, plan, sink, backend, prepared, renewal_settings


async def test_live_projection_is_reissued_before_the_app_token_lapses(
    app_context, monkeypatch, tmp_path
):
    realizer, request, plan, sink, backend, prepared, settings = (
        await _prepared_live_host(monkeypatch, app_context, tmp_path)
    )
    first_stamp, first_token = backend.projected()
    assert first_token == "ghs_first_issuance"
    expires_at = datetime.fromisoformat(prepared.github_issuance_expires_at)
    lifetime = (expires_at - datetime.now(timezone.utc)).total_seconds()
    assert 0 < lifetime <= _LIFETIME_SECONDS

    renewal = realizer._github_projection_renewal(
        request=request,
        plan=plan,
        sink=sink,
        settings=settings,
        expires_at=prepared.github_issuance_expires_at,
        renew_first=False,
    )
    assert renewal is not None
    # Scheduled before the issuance lapses, never after it.
    assert renewal.due_at - time.monotonic() < lifetime

    renewed = await realizer._refresh_github_projection(renewal)

    second_stamp, second_token = backend.projected()
    assert second_token == "ghs_second_issuance"
    assert second_stamp["ownerRef"] == first_stamp["ownerRef"]
    assert second_stamp["revision"] > first_stamp["revision"]
    assert renewed is not None and renewed.due_at > time.monotonic()
    # The same frozen binding: one App installation, repository and scope.
    first, second = app_context.issued[-2:]
    assert first == second
    assert second["repositories"] == ["MoonMind"]
    # Cleanup authority names the newest projection first, so retirement
    # matches the destination's current reservation.
    recorded = [
        item["projectionReservation"]
        for item in sink.binding.cleanupAuthorityRefs
        if isinstance(item, dict) and item.get("kind") == "github_credentials"
    ]
    assert recorded[-1] == second_stamp
    assert b"ambient-secret-canary" not in backend.writes


@pytest.mark.parametrize("change", ["disabled", "credential_changed"])
async def test_refused_reissue_never_replaces_the_live_projection(
    app_context, monkeypatch, tmp_path, change
):
    realizer, request, plan, sink, backend, prepared, settings = (
        await _prepared_live_host(monkeypatch, app_context, tmp_path)
    )
    live = backend.projected()
    issued = len(app_context.issued)
    async with app_context.sessions() as session:
        connection = await session.get(RepositoryConnectionRecord, _CONNECTION)
        if change == "disabled":
            connection.lifecycle = "disabled"
        else:
            # Another installation/key now backs the record: a new revision.
            connection.credential_revision += 1
        await session.commit()
    renewal = realizer._github_projection_renewal(
        request=request,
        plan=plan,
        sink=sink,
        settings=settings,
        expires_at=prepared.github_issuance_expires_at,
        renew_first=False,
    )

    retry = await realizer._refresh_github_projection(renewal)

    assert backend.projected() == live
    assert len(app_context.issued) == issued
    assert len(backend.writes) == 1
    # The session keeps running and retries while the current token is valid.
    assert retry is not None
    assert retry.due_at - time.monotonic() <= realizer._heartbeat_interval + 1
