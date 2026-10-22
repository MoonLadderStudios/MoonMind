"""Real job transports admit the converted operator without a person."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import container_jobs
from api_service.auth_providers import instance_operator_principal
from api_service.db.models import Base, ContainerJobRecord, User
from api_service.services.container_jobs import ContainerJobService
from api_service.services.single_user_conversion import SingleUserConversionRun
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security import auth_modes_4120
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from tests.unit.api.test_container_jobs_transport import _submit_arguments

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def admitted_jobs(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/admission.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            SingleUserConversionRun(
                preflight_digest="fresh",
                status="complete",
                result_json={
                    "published": True,
                    "decision": {"disposition": "fresh_init"},
                },
            )
        )
        await session.commit()
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(
        container_jobs.settings.feature_flags, "container_jobs_enabled", True
    )
    monkeypatch.setattr(
        container_jobs.settings.security, "JWT_SECRET_KEY", "job-test-secret"
    )
    for key in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    temporal = AsyncMock()
    app = FastAPI()
    app.include_router(container_jobs.router)

    async def sessions():
        async with factory() as session:
            original_get = session.get

            async def get(model, *args, **kwargs):
                assert model is not User, "job admission must not resolve a person"
                return await original_get(model, *args, **kwargs)

            session.get = get
            yield session

    app.dependency_overrides[container_jobs.get_async_session] = sessions
    for module in (container_jobs,):
        monkeypatch.setattr(module, "get_temporal_artifact_service", lambda _: None)
        monkeypatch.setattr(
            module,
            "ContainerJobService",
            lambda session, **kw: ContainerJobService(session, temporal=temporal, **kw),
        )
    yield SimpleNamespace(app=app, factory=factory, temporal=temporal)
    await engine.dispose()


async def call(client, operation, job_id=None):
    arguments = (
        _submit_arguments("http") if operation == "submit" else {"jobId": job_id}
    )
    if operation == "cancel":
        arguments["idempotencyKey"] = "cancel-1"
    path = "/api/v1/container-jobs"
    if operation == "submit":
        return await client.post(path, json=arguments)
    path += f"/{job_id}"
    if operation != "status":
        path += f"/{operation}"
    if operation == "cancel":
        return await client.post(path, json={"idempotencyKey": "cancel-1"})
    return await client.get(path)


@pytest.mark.parametrize("retained", [False, True])
async def test_account_free_job_lifecycle_and_owner_isolation(admitted_jobs, retained):
    ctx = admitted_jobs
    async with ctx.factory() as session:
        assert (await session.execute(select(User))).scalars().all() == []
        repository = ContainerJobService(session).repository
        foreign, _ = await repository.create_or_replay(
            owner=OwnerIdentity(
                principalId="retained-other-owner", principalType="user"
            ),
            request=ContainerJobSubmitRequest.model_validate(_submit_arguments("http")),
        )
        foreign_id = foreign.job_id
        retained_id = None
        if retained:
            old_job, _ = await repository.create_or_replay(
                owner=OwnerIdentity(
                    principalId=instance_operator_principal(), principalType="user"
                ),
                request=ContainerJobSubmitRequest.model_validate(
                    _submit_arguments("http")
                ),
            )
            retained_id = old_job.job_id
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app), base_url="http://localhost"
    ) as client:
        response = await call(client, "submit")
        assert response.status_code == 200, response.text
        result = response.json().get("result", response.json())
        job_id = result["jobId"]
        if retained:
            assert job_id == retained_id
        replay = await call(client, "submit")
        assert replay.status_code == 200, replay.text
        replay_result = replay.json().get("result", replay.json())
        assert replay_result["jobId"] == job_id
        assert replay_result["replayed"] is True
        # Recovery may repeat the deterministic Temporal start, not job identity.
        assert {
            call.args[0].job_id
            for call in ctx.temporal.start_container_job.await_args_list
        } == {job_id}
        for operation in ("status", "logs", "artifacts", "cancel"):
            response = await call(client, operation, job_id)
            assert response.status_code == 200, response.text
            denied = await call(client, operation, foreign_id)
            assert denied.status_code == 404, denied.text
    async with ctx.factory() as session:
        assert (await session.execute(select(User))).scalars().all() == []
        own = await session.get(ContainerJobRecord, job_id)
        assert own.owner_id == instance_operator_principal()
        foreign = await session.get(ContainerJobRecord, foreign_id)
        assert foreign.state == "queued"


@pytest.mark.parametrize("attack", ["direct", "cross-site", "loopback"])
@pytest.mark.parametrize("valid", [False, True])
async def test_runtime_credentials_never_admit_operator_jobs(
    admitted_jobs, attack, valid
):
    ctx = admitted_jobs
    peer = "172.18.0.9" if attack == "direct" else "127.0.0.1"
    token = (
        mint_container_job_session_capability(
            secret="job-test-secret",
            owner=OwnerIdentity(principalId="workflow-1", principalType="service"),
            agent_run_id="run-1",
            workflow_id="workflow-1",
            session_id="session-1",
            runtime_id="codex_cli",
            lifetime_seconds=300,
        )
        if valid
        else "invalid-runtime-capability"
    )
    headers = {"Authorization": f"Bearer {token}", "X-Moonmind-User": "operator"}
    if attack == "cross-site":
        headers["Origin"] = "https://attacker.example"
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app, client=(peer, 32123)),
        base_url="http://localhost",
        headers=headers,
    ) as client:
        response = await call(client, "submit")
    assert response.status_code in {401, 403}, response.text
    ctx.temporal.start_container_job.assert_not_awaited()


async def test_unpublished_conversion_does_not_create_operator_access(admitted_jobs):
    ctx = admitted_jobs
    async with ctx.factory() as session:
        row = (await session.execute(select(SingleUserConversionRun))).scalar_one()
        row.result_json = {
            "published": False,
            "decision": {"disposition": "fresh_init"},
        }
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app), base_url="http://localhost"
    ) as client:
        response = await call(client, "submit")
    assert response.status_code == 503, response.text
    ctx.temporal.start_container_job.assert_not_awaited()


async def test_trusted_ingress_preserves_operator_assertion_not_bearer_fallback(
    admitted_jobs, monkeypatch
):
    ctx = admitted_jobs
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "127.0.0.1/32")
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app),
        base_url="https://operator.example",
        headers={
            "X-Moonmind-User": "verified-operator",
            "Origin": "https://operator.example",
        },
    ) as client:
        allowed = await call(client, "submit")
        assert allowed.status_code == 200, allowed.text
        ctx.temporal.start_container_job.reset_mock()
        for name, value in (
            ("Authorization", "Bearer invalid"),
            ("X-MoonMind-Worker-Token", "invalid"),
            ("X-MoonMind-Execution-Fanout", "v1"),
        ):
            client.headers[name] = value
            denied = await call(client, "submit")
            assert denied.status_code == 401, denied.text
            del client.headers[name]
        ctx.temporal.start_container_job.assert_not_awaited()
