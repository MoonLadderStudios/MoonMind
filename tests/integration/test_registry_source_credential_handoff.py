"""Replay the private-image incident across API, workflow, Activity and Docker."""

import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.container_jobs import (
    ContainerJobAuthorizationError,
    ContainerJobService,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.registry_auth_resolve import RegistryCredential
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
REPLAY = json.loads(
    (
        Path(__file__).parent
        / "reliability/replays/registry-source-credential-handoff/manifest.json"
    ).read_text()
)
IMAGE = REPLAY["image"]
CREDENTIAL = "db://registry-pull"
DIGEST = "sha256:" + "a" * 64


def backend_settings(*, pull_policy=None, credential=CREDENTIAL, image=IMAGE):
    source = {
        "sourceRef": "build-image",
        "image": image,
        "registryCredentialRef": credential,
    }
    if pull_policy is not None:
        source["pullPolicy"] = pull_policy
    return resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps([source])}
    )


def authorizer(credential_ref=CREDENTIAL):
    return PrivateImageAuthorizationService(
        PrivateImageAuthorizationPolicy.model_validate(
            {
                "grants": [
                    {
                        "credentialRef": credential_ref,
                        "registry": "ghcr.io",
                        "repositories": ["moonladderstudios/tactics-ue-base"],
                        "principals": ["service:agent"],
                    }
                ]
            }
        )
    )


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield db
    finally:
        await engine.dispose()


async def submit(
    session, *, config, principal="agent", direct=False, credential_ref=CREDENTIAL
):
    temporal = AsyncMock()
    spec = (
        {"image": IMAGE, "registryCredentialRef": CREDENTIAL}
        if direct
        else {"imageSourceRef": "build-image"}
    )
    request = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "registry-handoff",
            "source": {"source": "workflow"},
            "spec": {
                **spec,
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
                "command": ["true"],
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            },
        }
    )
    await ContainerJobService(
        session,
        temporal=temporal,
        authorizer=authorizer(credential_ref),
        backend_settings=config,
    ).submit(
        owner=OwnerIdentity(principalId=principal, principalType="service"),
        request=request,
    )
    return temporal.start_container_job.await_args.args[0]


@pytest.mark.parametrize(
    "policy,direct,credential_backend",
    [
        (None, False, "stub"),
        ("if-missing", False, "stub"),
        (None, True, "stub"),
        (None, False, "env"),
        (None, False, "db"),
    ],
)
async def test_authorized_cold_pull_and_cache_cross_production_handoff(
    session, tmp_path, monkeypatch, policy, direct, credential_backend
):
    credential_ref = CREDENTIAL
    material = json.dumps({"username": "fixture-user", "password": "fixture-secret"})
    if credential_backend == "env":
        # A new deployment can use its provisioner's secret without a DB import.
        credential_ref = "env://FIXTURE_REGISTRY_AUTH"
        monkeypatch.setenv("FIXTURE_REGISTRY_AUTH", material)
    elif credential_backend == "db":
        from api_service.services.secrets import SecretsService
        from moonmind.auth.resolvers import db_resolver

        await SecretsService.create_secret(session, "registry-pull", material)
        # Resolve through a separate session, as a recreated worker does.
        monkeypatch.setattr(
            db_resolver, "async_session_maker", async_sessionmaker(session.bind)
        )
    config = backend_settings(pull_policy=policy, credential=credential_ref)
    inp = await submit(
        session, config=config, direct=direct, credential_ref=credential_ref
    )
    assert inp.registry_authorization.credential_ref == credential_ref
    assert inp.request.spec.registry_credential_ref == (CREDENTIAL if direct else None)
    (tmp_path / "temporal_sandbox/run/repo").mkdir(parents=True)
    commands, auth_dirs = [], []
    cached = False

    async def runner(raw):
        nonlocal cached
        args = tuple(raw)
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return (0, DIGEST.encode(), b"") if cached else (1, b"", b"No such image")
        if "pull" in args:
            assert args[0] == "--config", "must not silently retry anonymously"
            auth_dir = Path(args[1])
            auth_dirs.append(auth_dir)
            config_path = auth_dir / "config.json"
            assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
            auth = json.loads(config_path.read_text())["auths"]
            assert auth == {
                "ghcr.io": {"username": "fixture-user", "password": "fixture-secret"}
            }
            assert args[-1] == IMAGE
            cached = True
        if args[0] == "info":
            return 0, f"{10 * 1024**3}\t16".encode(), b""
        if args[:2] == ("inspect", "--format"):
            if args[2] == "{{json .State}}":
                return 0, b'{"Running":false,"ExitCode":0}', b""
            return 1, b"", b"Error: No such object: moonmind-container-job"
        return 0, b"", b""

    resolver = AsyncMock(
        return_value=RegistryCredential(
            username="fixture-user", secret="fixture-secret"
        )
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=config,
        command_runner=runner,
        registry_auth_resolver=resolver if credential_backend == "stub" else None,
        auth_root=tmp_path / "auth",
        evidence_publisher=AsyncMock(return_value="art:evidence"),
        projection_writer=AsyncMock(),
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)

    async def dispatch(name, payload, **kwargs):
        return await getattr(runtime, name.replace(".", "_"))(payload)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.container_job.workflow.execute_activity",
        dispatch,
    )
    for _ in range(2):
        result = await MoonMindContainerJobWorkflow().run(
            inp.model_dump(mode="json", by_alias=True)
        )
        assert result["state"] == "succeeded", result
        assert result["terminal"]["exitCode"] == 0
        assert result["cleanup"]["state"] == "succeeded"
    if credential_backend == "stub":
        resolver.assert_awaited_once_with(CREDENTIAL)
    else:
        resolver.assert_not_awaited()
    assert len(auth_dirs) == 1 and not auth_dirs[0].exists()
    assert all("fixture-secret" not in str(command) for command in commands)


@pytest.mark.parametrize(
    "principal,credential", [("other", CREDENTIAL), ("agent", None)]
)
async def test_unauthorized_source_fails_before_workflow(
    session, principal, credential
):
    with pytest.raises(ContainerJobAuthorizationError):
        await submit(
            session, config=backend_settings(credential=credential), principal=principal
        )


@pytest.mark.parametrize(
    "change",
    ["credential", "removed_credential", "image", "missing_decision", "unknown_source"],
)
async def test_changed_or_legacy_authority_fails_before_docker(
    session, tmp_path, monkeypatch, change
):
    inp = await submit(session, config=backend_settings())
    config = backend_settings()
    if change == "credential":
        config = backend_settings(credential="db://different")
    elif change == "removed_credential":
        config = backend_settings(credential=None)
    elif change == "image":
        config = backend_settings(image=IMAGE.split("@")[0] + ":different")
    elif change == "unknown_source":
        config = resolve_container_backend_settings({})
    else:
        # Previously persisted source submissions had no authorization payload.
        inp.registry_authorization = None
    runner = AsyncMock()
    resolver = AsyncMock()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=config,
        command_runner=runner,
        registry_auth_resolver=resolver,
        auth_root=tmp_path / "auth",
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError) as error:
        await runtime.container_job_acquire_image(
            {
                "jobId": inp.job_id,
                "ownershipToken": inp.ownership_token,
                "request": inp.request.model_dump(mode="json", by_alias=True),
                "registryAuthorization": inp.registry_authorization.model_dump(
                    by_alias=True
                )
                if inp.registry_authorization
                else None,
                "resolvedWorkspaceRef": str(tmp_path),
            }
        )
    assert error.value.non_retryable
    runner.assert_not_awaited()
    resolver.assert_not_awaited()
