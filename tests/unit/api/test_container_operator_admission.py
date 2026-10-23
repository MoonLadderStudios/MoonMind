"""Real job transports admit the converted operator without a person."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import container_jobs, mcp_tools
from api_service.auth_providers import instance_operator_principal
from api_service.db.models import Base, ContainerJobRecord
from api_service.services.container_jobs import ContainerJobService
from api_service.services.single_user_conversion import SingleUserConversionRun
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from tests.helpers.account_source import User
from tests.unit.api.test_container_jobs_transport import _submit_arguments

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def admitted_jobs(tmp_path, monkeypatch, request):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/admission.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retained_id = None
    retained_owner = None
    if getattr(request, "param", None) == "predecessor":
        from tests.helpers.account_source import create_source_schema

        async with engine.begin() as connection:
            await connection.run_sync(create_source_schema)
        retained_owner = str(uuid4())
        async with factory() as session:
            session.add(User(id=UUID(retained_owner), email="retained@example.invalid"))
            record, _ = await ContainerJobService(session).repository.create_or_replay(
                owner=OwnerIdentity(principalId=retained_owner, principalType="user"),
                request=ContainerJobSubmitRequest.model_validate(
                    _submit_arguments("http")
                ),
            )
            record.state = "succeeded"
            record.terminal_outcome_json = {"exitCode": 0}
            retained_id = record.job_id
            await session.commit()
    async with factory() as session:
        from api_service.services.single_user_conversion import run_guarded_upgrade

        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published

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
    app.include_router(mcp_tools.router, prefix="/api")

    async def sessions():
        async with factory() as session:
            original_get = session.get

            async def get(model, *args, **kwargs):
                assert model is not User, "job admission must not resolve a person"
                return await original_get(model, *args, **kwargs)

            session.get = get
            yield session

    app.dependency_overrides[container_jobs.get_async_session] = sessions
    for module in (container_jobs, mcp_tools):
        monkeypatch.setattr(module, "get_temporal_artifact_service", lambda _: None)
        monkeypatch.setattr(
            module,
            "ContainerJobService",
            lambda session, **kw: ContainerJobService(session, temporal=temporal, **kw),
        )
    yield SimpleNamespace(
        app=app,
        factory=factory,
        temporal=temporal,
        retained_id=retained_id,
        retained_owner=retained_owner,
    )
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
        assert "user" not in Base.metadata.tables
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
        assert "user" not in Base.metadata.tables
        own = await session.get(ContainerJobRecord, job_id)
        assert own.owner_id == instance_operator_principal()
        foreign = await session.get(ContainerJobRecord, foreign_id)
        assert foreign.state == "queued"


@pytest.mark.parametrize("admitted_jobs", ["predecessor"], indirect=True)
async def test_converted_nonzero_owner_remains_accessible_and_replays(admitted_jobs):
    ctx = admitted_jobs
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app), base_url="http://localhost"
    ) as client:
        for operation in ("status", "logs", "artifacts", "cancel"):
            response = await call(client, operation, ctx.retained_id)
            assert response.status_code == 200, response.text
        replay = await call(client, "submit")
        assert replay.status_code == 200, replay.text
        assert replay.json()["jobId"] == ctx.retained_id
        assert replay.json()["replayed"] is True
        changed = _submit_arguments("http")
        changed["spec"]["image"] = "different"
        conflict = await client.post("/api/v1/container-jobs", json=changed)
        assert conflict.status_code == 409, conflict.text
    ctx.temporal.start_container_job.assert_not_awaited()
    ctx.temporal.signal_container_job_cancel.assert_not_awaited()
    async with ctx.factory() as session:
        original = await session.get(ContainerJobRecord, ctx.retained_id)
        assert original.owner_id == ctx.retained_owner
        assert original.owner_type == "user"
        assert original.state == "succeeded"
        assert len((await session.scalars(select(ContainerJobRecord))).all()) == 1
    # A correctly signed machine capability with the same owner still cannot
    # cross its persisted session/source boundary to this HTTP-origin job.
    token = mint_container_job_session_capability(
        secret="job-test-secret",
        owner=OwnerIdentity(principalId=ctx.retained_owner, principalType="user"),
        agent_run_id="run-other",
        workflow_id="workflow-other",
        session_id="session-other",
        runtime_id="codex_cli",
        lifetime_seconds=300,
    )
    async with AsyncClient(
        transport=ASGITransport(app=ctx.app), base_url="http://localhost"
    ) as client:
        for operation in ("status", "logs", "artifacts", "cancel"):
            args = {"jobId": ctx.retained_id}
            if operation == "cancel":
                args["idempotencyKey"] = "machine-cancel"
            denied = await client.post(
                "/api/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": f"container.{operation}", "arguments": args},
            )
            assert denied.status_code == 404, denied.text


@pytest.mark.parametrize("kind", ["unknown", "malformed", "mixed"])
async def test_retained_job_grants_require_complete_eligible_source(tmp_path, kind):
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from tests.helpers.account_source import create_source_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/refused.db")
    async with engine.begin() as connection:
        await connection.run_sync(create_source_schema)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = str(uuid4()) if kind != "malformed" else "unresolved-person"
    try:
        async with factory() as session:
            if kind == "mixed":
                session.add_all(
                    [
                        User(id=UUID(owner), email="first@example.invalid"),
                        User(id=uuid4(), email="second@example.invalid"),
                    ]
                )
            job, _ = await ContainerJobService(session).repository.create_or_replay(
                owner=OwnerIdentity(principalId=owner, principalType="user"),
                request=ContainerJobSubmitRequest.model_validate(
                    _submit_arguments("http")
                ),
            )
            job_id = job.job_id
            await session.commit()
            result = await run_guarded_upgrade(session, operator_authorized=True)
            assert result.published is False
        async with factory() as session:
            job = await session.get(ContainerJobRecord, job_id)
            assert job.operator_access_granted is False
            assert job.owner_id == owner and job.state == "queued"
            assert (await session.scalars(select(SingleUserConversionRun))).all() == []
    finally:
        await engine.dispose()


async def test_retained_alias_jobs_with_same_key_are_never_chosen_by_preference(
    tmp_path,
):
    from api_service.services.container_jobs import ContainerJobIdempotencyConflictError
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.security.operator_admission import OperatorAdmission
    from tests.helpers.account_source import create_source_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/aliases.db")
    async with engine.begin() as connection:
        await connection.run_sync(create_source_schema)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owners = [uuid4(), uuid4()]
    request = ContainerJobSubmitRequest.model_validate(_submit_arguments("http"))
    try:
        async with factory() as session:
            session.add_all(
                [User(id=owner, email=f"{owner}@example.invalid") for owner in owners]
            )
            for owner in owners:
                await ContainerJobService(session).repository.create_or_replay(
                    owner=OwnerIdentity(principalId=str(owner), principalType="user"),
                    request=request,
                )
            await session.commit()
            result = await run_guarded_upgrade(
                session,
                operator_authorized=True,
                alias_groups=[[str(owner) for owner in owners]],
            )
            assert result.published
        async with factory() as session:
            service = ContainerJobService(
                session, operator=OperatorAdmission(via="loopback")
            )
            with pytest.raises(ContainerJobIdempotencyConflictError):
                await service.submit(
                    owner=OwnerIdentity(
                        principalId=instance_operator_principal(), principalType="user"
                    ),
                    request=request,
                )
            rows = (await session.scalars(select(ContainerJobRecord))).all()
            assert len(rows) == 2
            assert {row.owner_id for row in rows} == {str(owner) for owner in owners}
    finally:
        await engine.dispose()


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
