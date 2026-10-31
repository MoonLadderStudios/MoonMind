"""Selected Enterprise authority reaches acquisition and host delivery."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from api_service.db import base
from api_service.services import omnigent_execution_plan_service as writer
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.config.settings import settings
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.workflows.executions.repository_contract import (
    RepositoryIdentity,
    normalize_endpoint,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
)
from tests.integration.reliability.test_repository_authority_handoffs_4009 import (
    _REPO,
    _compile,
    _request,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _ready_opencode_image_pair,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest_asyncio.fixture
async def authority_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTED_REPOSITORY_PAT", "selected-secret-canary")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret-canary")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setattr(
        writer, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        writer, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "artifacts")),
    )
    async with temporal_db(tmp_path) as sessions:
        monkeypatch.setattr(base, "async_session_maker", sessions)
        artifacts = _OnDemandTemporalArtifactService(sessions)

        async def read_repository_access_snapshot(ref, *, request):
            _metadata, payload = await artifacts.read(
                artifact_id=ref.removeprefix("artifact:"),
                access=access_for_test("user-1"),
            )
            return payload

        yield sessions, artifacts, SimpleNamespace(
            read_repository_access_snapshot=read_repository_access_snapshot,
        )


_ENDPOINT = "https://github.enterprise.test"
_REMOTE = f"{_ENDPOINT}/{_REPO}.git"


async def _enterprise_plan(
    context, monkeypatch, *, endpoint=_ENDPOINT, canonical_remote=_REMOTE
):
    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    connection = github_pat_connection(
        "selected-enterprise", "SELECTED_REPOSITORY_PAT"
    ).model_copy(update={"endpoint_ref": endpoint})
    assignment = github_repository_assignment("selected-enterprise", _REPO).model_copy(
        update={
            "identity": RepositoryIdentity(
                endpoint=endpoint, canonicalRemote=canonical_remote, displayName=_REPO
            )
        }
    )
    sessions, artifacts, gateway = context
    async with sessions() as session:
        connections = RepositoryConnectionService(session)
        await connections.create_connection(
            connection,
            actor_ref="system:deployment",
            request_id="enterprise-connection",
            principal_ref="system:deployment",
            principal_scope=("system", None),
        )
        await connections.set_assignment(
            assignment,
            actor_ref="system:deployment",
            request_id="enterprise-assignment",
            principal_ref="system:deployment",
            principal_scope=("system", None),
        )
    target = {
        "provider": "git",
        "connectionRef": connection.id,
        "repository": {"name": _REPO},
        "branch": {"name": "main"},
    }
    from api_service.api.routers.executions import (
        _build_original_workflow_input_snapshot_payload,
    )

    snapshot = _build_original_workflow_input_snapshot_payload(
        source_kind="create",
        payload={
            "repository": target,
            "targetRuntime": "omnigent",
            "requiredCapabilities": ["gh"],
        },
        task_payload={"instructions": "Use durable refs only."},
    )
    input_ref, input_digest = await writer.persist_json_artifact(
        artifact_service=artifacts,
        principal="user-1",
        artifact_class="original_task_input_snapshot",
        payload=snapshot,
    )
    compiled = await _compile(
        monkeypatch,
        context,
        repository=target,
        requiredCapabilities=["gh"],
        task_input_snapshot_ref=input_ref,
        task_input_snapshot_digest=input_digest,
    )
    closure = await artifacts.verified_dependencies(
        root_artifact_id=compiled.binding.plan_artifact_ref,
        access=access_for_test("user-1"),
    )
    assert closure is not None
    assert input_ref in {entry.artifact_id for entry in closure.dependencies}
    request = _request().model_copy(
        update={"workspace_spec": {"repositoryTarget": target}}
    )
    return compiled.envelope, request, sessions, gateway


@pytest.mark.parametrize(
    "endpoint,canonical_remote",
    [
        (_ENDPOINT, _REMOTE),
        (f"{_ENDPOINT}/api/v3", _REMOTE),
        (f"{_ENDPOINT}:443", f"{_ENDPOINT}:443/{_REPO}.git"),
    ],
)
async def test_enterprise_snapshot_reaches_connection_bound_acquisition(
    authority_context, monkeypatch, endpoint, canonical_remote
):
    plan, request, sessions, gateway = await _enterprise_plan(
        authority_context,
        monkeypatch,
        endpoint=endpoint,
        canonical_remote=canonical_remote,
    )
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan, request=request, role="source_read", operation="read"
    )
    try:
        assert acquired.binding.connection_id == "selected-enterprise"
        assert acquired.binding.endpoint == normalize_endpoint(endpoint)
        assert acquired.credential.use_now(bytes) == b"selected-secret-canary"
    finally:
        acquired.credential.clear()


async def test_enterprise_clone_uses_snapshot_canonical_remote(
    authority_context, monkeypatch, tmp_path
):
    plan, request, sessions, gateway = await _enterprise_plan(
        authority_context, monkeypatch
    )
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    calls = []

    async def runner(argv, input_bytes=None):
        calls.append((argv, input_bytes))
        if input_bytes is not None:
            target = tmp_path / argv[-3].removeprefix("/work/")
            (target / ".git").mkdir(parents=True)
            (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        return 0, "", ""

    workspace = OmnigentWorkspaceMaterializer(
        command_runner=runner,
        workspace_root=tmp_path,
        repository_credential_service=credentials,
    )
    request = request.model_copy(
        update={
            "workspace_spec": {
                **request.workspace_spec,
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": hashlib.sha256(
                        f"{request.correlation_id}:{request.idempotency_key}".encode()
                    ).hexdigest()[:24],
                    "relativePath": "repo",
                },
            }
        }
    )
    attachment = await workspace.materialize(
        request,
        plan=plan,
        runtime_uid=os.getuid() or 1000,
        runtime_gid=os.getgid() or 1000,
    )
    assert attachment["kind"] == "bind"
    clone_argv, token = calls[0]
    assert _REMOTE in clone_argv
    assert token == b"selected-secret-canary"
    assert "selected-secret-canary" not in json.dumps(clone_argv)


async def test_enterprise_cli_projection_uses_admitted_host(
    authority_context, monkeypatch, tmp_path
):
    plan, request, sessions, gateway = await _enterprise_plan(
        authority_context, monkeypatch
    )
    owner_ref = "enterprise-lease-owner"
    config_dir = tmp_path / "gh-config"
    runtime_uid = os.getuid() or 1000
    runtime_gid = os.getgid() or 1000
    calls = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1:3] == ["volume", "inspect"]:
                return 0, hashlib.sha256(owner_ref.encode()).hexdigest()[:32], ""
            if argv[1] == "run":
                script_index = argv.index("-ceu") + 1
                script = argv[script_index].replace("/config", str(config_dir))
                # Exercise the real atomic writer and ownership checks with
                # this process's identity; CI runners need not use UID 1000.
                completed = subprocess.run(
                    ["sh", "-ceu", script, *argv[script_index + 1 :]],
                    input=kwargs.get("input_bytes"),
                    capture_output=True,
                    check=True,
                )
                return (
                    completed.returncode,
                    completed.stdout.decode(),
                    completed.stderr.decode(),
                )
            return 0, "", ""

    credentials = OmnigentGithubCredentialService(
        Backend(), session_factory=sessions, artifact_gateway=gateway
    )
    anticipated = await credentials.anticipated_attachment_for_request(
        plan.payload.resolvedTools, plan=plan, request=request, owner_ref=owner_ref
    )
    attachment = await credentials.materialize(
        request=request,
        resolved_tools=plan.payload.resolvedTools,
        owner_ref=owner_ref,
        writer_image_ref="test-writer-image",
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
        plan=plan,
        projection_reservation={
            "ownerRef": owner_ref,
            "revision": 1,
            "reservationId": "00000000-0000-4000-8000-000000000001",
        },
        projection_verifier=AsyncMock(),
    )
    assert all(attachment[key] == value for key, value in anticipated.items())
    assert attachment["accessMode"] == "read-only"
    hosts = (config_dir / "hosts.yml").read_text()
    assert hosts.split("\n", 1)[1].startswith("github.enterprise.test:\n")
    assert "oauth_token: selected-secret-canary\n" in hosts
    assert "ambient-secret-canary" not in hosts
    assert "selected-secret-canary" not in json.dumps([argv for argv, _ in calls])
    for path, mode in ((config_dir, 0o700), (config_dir / "hosts.yml", 0o600)):
        metadata = path.stat()
        assert (metadata.st_uid, metadata.st_gid) == (runtime_uid, runtime_gid)
        assert metadata.st_mode & 0o777 == mode


@pytest.mark.parametrize(
    "repository",
    [
        f"https://github.com/{_REPO}.git",
        f"https://github.enterprise.test.evil/{_REPO}.git",
        "https://github.enterprise.test/another/repository.git",
        f"https://embedded:credential@github.enterprise.test/{_REPO}.git",
    ],
)
async def test_enterprise_snapshot_rejects_another_host_or_target(
    authority_context, monkeypatch, repository
):
    plan, request, sessions, gateway = await _enterprise_plan(
        authority_context, monkeypatch
    )
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    with pytest.raises(ValueError, match="target conflicts"):
        await credentials.acquire_repository_use(
            plan=plan,
            request=request,
            role="source_read",
            operation="read",
            repository=repository,
        )


async def test_enterprise_snapshot_cannot_add_a_trusted_host(
    authority_context, monkeypatch
):
    plan, request, sessions, gateway = await _enterprise_plan(
        authority_context, monkeypatch
    )
    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "")
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    with pytest.raises(ValueError, match="allowlisted"):
        await credentials.acquire_repository_use(
            plan=plan, request=request, role="source_read", operation="read"
        )
