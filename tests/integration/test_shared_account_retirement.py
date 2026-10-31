"""GH4354: historical Alembic, populated conversion and real API process restart."""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.core.encryption import get_encryption_key
from api_service.db.models import (
    ContainerJobRecord,
    ManagedSecret,
    TemporalArtifact,
    WorkflowRun,
)
from api_service.services.single_user_conversion import SingleUserConversionRun
from moonmind.schemas.container_job_models import (
    ContainerJobArtifactPage,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.helpers.account_source import User, UserProfile
from tests.integration import test_single_user_conversion_postgres_4346 as source_pg
from tests.unit.api.test_container_jobs_transport import _submit_arguments

conversion_postgres_url = source_pg.conversion_postgres_url

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def serve_api(env, tmp_path, attempt):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_path = tmp_path / f"api-{attempt}.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.helpers.account_free_api_process"],
            cwd=ROOT,
            env={**env, "GH4354_PORT": str(port)},
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False
            ) as client:
                async with asyncio.timeout(45):
                    while True:
                        assert process.poll() is None, log_path.read_text()[-3000:]
                        try:
                            await client.get("/healthz")
                            break
                        except httpx.TransportError:
                            await asyncio.sleep(0.05)
                yield client
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=10)


@pytest.mark.parametrize(
    "people", [0, 1, 2], ids=["fresh", "populated", "mixed-refused"]
)
async def test_historical_upgrade_and_process_restart_preserve_state(
    conversion_postgres_url,
    tmp_path,
    people,
):
    schema = "gh4354_" + uuid4().hex
    url = make_url(conversion_postgres_url)
    admin = create_async_engine(conversion_postgres_url)
    engine = create_async_engine(
        conversion_postgres_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        **os.environ,
        "POSTGRES_HOST": url.host,
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_USER": url.username,
        "POSTGRES_PASSWORD": url.password or "",
        "POSTGRES_DB": url.database,
        "PGOPTIONS": f"-csearch_path={schema}",
        "GH4354_DATABASE_URL": conversion_postgres_url,
        "GH4354_SCHEMA": schema,
        "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
        "MOONMIND_PUBLIC_BASE_URL": "",
        "MOONMIND_TRUSTED_INGRESS": "",
        "MOONMIND_TRUSTED_PROXIES": "",
        "ENCRYPTION_MASTER_KEY": get_encryption_key(),
        "AUTH_PROVIDER": "",
        "MOONMIND_CONTAINER_JOBS_ENABLED": "true",
        "GH4354_ARTIFACT_ROOT": str(tmp_path / "job-evidence"),
    }
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        # Exercise the applied historical chain using only this disposable schema.
        migrated = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-c",
                "import sys; sys.modules['fastapi_users_db_sqlalchemy'] = None; from alembic.config import main; main()",
                "-c",
                "api_service/migrations/alembic.ini",
                "upgrade",
                "398_instance_settings" if people else "head",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert migrated.returncode == 0, migrated.stderr[-4000:]
        owners = [uuid4() for _ in range(people)]
        raw_profile = None
        retained_run_id = uuid4()
        retained_job_id = "container-job:" + uuid4().hex
        submission = ContainerJobSubmitRequest.model_validate(_submit_arguments("http"))
        request_json = submission.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        async with sessions() as session:
            for index, owner in enumerate(owners):
                session.add(User(id=owner, email=f"source-{index}@example.invalid"))
            await session.flush()
            if owners:
                session.add(
                    UserProfile(
                        user_id=owners[0],
                        openai_api_key_encrypted="retained-test-credential",
                    )
                )

                async def publish_historical_evidence(name, payload, content_type):
                    # Seed the predecessor wire/storage shape, not today's ORM:
                    # dependency-admission columns do not exist until upgrade.
                    artifact_id = "art_" + uuid4().hex
                    storage_key = "historical/" + artifact_id
                    LocalTemporalArtifactStore(tmp_path / "job-evidence").write_bytes(
                        storage_key, payload, content_type=content_type
                    )
                    await session.execute(
                        text("""
                            INSERT INTO temporal_artifacts
                                (artifact_id, created_by_principal, content_type,
                                 size_bytes, sha256, storage_backend, storage_key,
                                 status, metadata)
                            VALUES (:id, :owner, :content_type, :size, :sha256,
                                    'local_fs', :storage_key, 'complete',
                                    CAST(:metadata AS jsonb))
                        """),
                        {
                            "id": artifact_id,
                            "owner": f"user:{owners[0]}",
                            "content_type": content_type,
                            "size": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "storage_key": storage_key,
                            "metadata": json.dumps(
                                {
                                    "artifact_type": "container_job.evidence",
                                    "name": name,
                                    "container_job_id": retained_job_id,
                                }
                            ),
                        },
                    )
                    return artifact_id

                logs_ref = await publish_historical_evidence(
                    "retained.log", b"retained job output\n", "application/octet-stream"
                )
                manifest = ContainerJobArtifactPage.model_validate(
                    {
                        "jobId": retained_job_id,
                        "artifacts": [
                            {"name": "retained.log", "artifactRef": logs_ref}
                        ],
                        "publication": {"state": "succeeded"},
                    }
                )
                artifacts_ref = await publish_historical_evidence(
                    "outputs.json",
                    manifest.model_dump_json(by_alias=True).encode(),
                    "application/json",
                )
                # Seed the predecessor schema before the new grant column exists.
                await session.execute(
                    text("""
                    INSERT INTO container_jobs
                        (job_id, contract_version, owner_id, owner_type, idempotency_key,
                         source_json, request_json, state, terminal_outcome_json,
                         publication_outcome_json, cleanup_outcome_json, logs_ref, artifacts_ref)
                    VALUES (:job, 'v1', :owner, 'user', :key, CAST(:source AS jsonb),
                            CAST(:request AS jsonb), 'succeeded', CAST(:terminal AS jsonb),
                            '{"state":"succeeded"}', '{"state":"not_attempted"}', :logs, :artifacts)
                """),
                    {
                        "job": retained_job_id,
                        "owner": str(owners[0]),
                        "key": submission.idempotency_key,
                        "source": json.dumps(request_json["source"]),
                        "request": json.dumps(request_json),
                        "logs": logs_ref,
                        "artifacts": artifacts_ref,
                        "terminal": json.dumps({"exitCode": 0}),
                    },
                )
                await session.execute(
                    text("""
                        INSERT INTO temporal_artifacts
                            (artifact_id, storage_key, created_by_principal, metadata)
                        VALUES ('retained', 'unchanged-key', :owner, '{}')
                    """),
                    {"owner": str(owners[0])},
                )
            if owners:
                session.add(
                    WorkflowRun(
                        id=retained_run_id,
                        feature_key="retained-history",
                        requested_by_user_id=owners[0],
                        created_by=owners[0],
                        branch_name="saved-work",
                    )
                )
            await session.commit()
            if owners:
                raw_profile = (
                    await session.execute(
                        text("SELECT openai_api_key_encrypted FROM user_profile")
                    )
                ).scalar_one()

        if owners:
            upgraded = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.modules['fastapi_users_db_sqlalchemy'] = None; from alembic.config import main; main()",
                    "-c",
                    "api_service/migrations/alembic.ini",
                    "upgrade",
                    "head",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert upgraded.returncode == 0, upgraded.stderr[-4000:]

        for attempt in (1, 2):
            async with serve_api(env, tmp_path, attempt) as client:
                response = await client.get("/api/v1/settings/catalog")
                assert response.status_code == (
                    503 if people == 2 else 200
                ), response.text
                for path in ("/me", "/api/v1/accounts/me", "/api/v1/oidc/login"):
                    assert (await client.get(path)).status_code == 404
                assert (
                    await client.post(
                        "/api/v1/accounts/login",
                        json={"email": "old", "password": "old"},
                    )
                ).status_code == 404
                assert (
                    await client.get(
                        "/api/v1/settings/catalog",
                        headers={"Authorization": "Bearer old-login"},
                    )
                ).status_code == 401
                if owners:
                    job_path = f"/api/v1/container-jobs/{retained_job_id}"
                    for suffix in ("", "/logs", "/artifacts"):
                        response = await client.get(job_path + suffix)
                        assert response.status_code == (
                            503 if people == 2 else 200
                        ), response.text
                        if people == 1 and suffix == "/logs":
                            assert (
                                response.json()["entries"][0]["text"]
                                == "retained job output"
                            )
                        if people == 1 and suffix == "/artifacts":
                            assert (
                                response.json()["artifacts"][0]["artifactRef"]
                                == logs_ref
                            )
                    if people == 1:
                        replay = await client.post(
                            "/api/v1/container-jobs", json=request_json
                        )
                        assert replay.status_code == 200, replay.text
                        assert replay.json()["jobId"] == retained_job_id
                        assert replay.json()["replayed"] is True
                        canceled = await client.post(
                            job_path + "/cancel",
                            json={"idempotencyKey": "retained-cancel"},
                        )
                        assert canceled.status_code == 200, canceled.text
                        assert canceled.json()["accepted"] is False
            async with engine.connect() as conn:
                tables = set(
                    await conn.run_sync(lambda c: inspect(c).get_table_names())
                )
            async with sessions() as session:
                completed = (
                    (await session.execute(select(SingleUserConversionRun)))
                    .scalars()
                    .all()
                )
                if people == 2:
                    assert "user" in tables and "retired_user" not in tables
                    assert completed == []
                    assert (
                        await session.get(TemporalArtifact, "retained")
                    ).operator_recovery_granted is False
                else:
                    assert "user" not in tables and "user_profile" not in tables
                    assert len(completed) == 1
                    assert completed[0].result_json["published"] is True
                if owners:
                    job = await session.get(ContainerJobRecord, retained_job_id)
                    assert job.owner_id == str(owners[0])
                    assert job.owner_type == "user"
                    assert job.operator_access_granted is (people == 1)
                    assert job.request_json == request_json
                    assert job.state == "succeeded"
                    assert job.cancel_idempotency_key is None
                    assert (
                        job.logs_ref == logs_ref and job.artifacts_ref == artifacts_ref
                    )
                    assert (
                        len((await session.scalars(select(ContainerJobRecord))).all())
                        == 1
                    )
                    retained_run = await session.get(WorkflowRun, retained_run_id)
                    assert retained_run.requested_by_user_id == owners[0]
                    assert retained_run.created_by == owners[0]
                    assert retained_run.branch_name == "saved-work"
                    table = "user_profile" if people == 2 else "retired_user_profile"
                    assert (
                        await session.execute(
                            text(f'SELECT openai_api_key_encrypted FROM "{table}"')
                        )
                    ).scalar_one() == raw_profile
                    artifact = await session.get(TemporalArtifact, "retained")
                    assert artifact.storage_key == "unchanged-key"
                    assert artifact.created_by_principal == str(owners[0])
                    if people == 1:
                        secret = (
                            await session.execute(
                                select(ManagedSecret).where(
                                    ManagedSecret.slug.like("legacy-user-profile-%")
                                )
                            )
                        ).scalar_one()
                        assert secret.ciphertext == "retained-test-credential"
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


pg_maker = source_pg.pg_maker


async def test_retirement_refuses_a_live_reader_without_losing_converted_state(
    pg_maker,
):
    from api_service.services.single_user_conversion import (
        ConcurrentConversionError,
        run_guarded_upgrade,
    )

    owner = uuid4()
    async with pg_maker() as seed:
        connection = await seed.connection()
        await connection.run_sync(lambda c: ContainerJobRecord.__table__.create(c))
        seed.add(User(id=owner, email="retained-reader@example.invalid"))
        seed.add(UserProfile(user_id=owner, openai_api_key_encrypted="reader-test-key"))
        from api_service.services.container_jobs import ContainerJobService

        job, _ = await ContainerJobService(seed).repository.create_or_replay(
            owner=OwnerIdentity(principalId=str(owner), principalType="user"),
            request=ContainerJobSubmitRequest.model_validate(_submit_arguments("http")),
        )
        job_id = job.job_id
        await seed.commit()
    async with pg_maker() as reader:
        assert (await reader.execute(select(User.id))).scalar_one() == owner
        async with pg_maker() as converting:
            with pytest.raises(ConcurrentConversionError):
                await asyncio.wait_for(
                    run_guarded_upgrade(converting, operator_authorized=True), timeout=4
                )
        await reader.rollback()
    async with pg_maker() as retry:
        assert (await retry.execute(select(User.id))).scalar_one() == owner
        assert (await retry.execute(select(ManagedSecret))).scalars().all() == []
        assert (
            await retry.execute(select(SingleUserConversionRun))
        ).scalars().all() == []
        assert (
            await retry.get(ContainerJobRecord, job_id)
        ).operator_access_granted is False
        await retry.rollback()
        assert (await run_guarded_upgrade(retry, operator_authorized=True)).published
    async with pg_maker() as verified:
        job = await verified.get(ContainerJobRecord, job_id)
        assert job.operator_access_granted is True
        assert job.owner_id == str(owner)


async def test_converted_running_job_preserves_temporal_handoff_and_cancel(
    pg_maker, monkeypatch, tmp_path
):
    """Exercise the real adapter/server; no lifecycle Activity is substituted."""
    from fastapi import FastAPI
    from httpx import ASGITransport
    from temporalio.testing import WorkflowEnvironment

    from api_service.api.routers import container_jobs
    from api_service.services.container_jobs import ContainerJobService
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.schemas.container_job_models import container_job_workflow_id
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    for name in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        container_jobs.settings.feature_flags, "container_jobs_enabled", True
    )
    owner = OwnerIdentity(principalId=str(uuid4()), principalType="user")
    request = ContainerJobSubmitRequest.model_validate(_submit_arguments("http"))
    async with await WorkflowEnvironment.start_time_skipping() as temporal:
        adapter = TemporalClientAdapter(temporal.client)
        async with pg_maker() as session:
            connection = await session.connection()
            await connection.run_sync(lambda c: ContainerJobRecord.__table__.create(c))
            from uuid import UUID

            session.add(
                User(id=UUID(owner.principal_id), email="job-owner@example.invalid")
            )
            await session.commit()
            accepted = await ContainerJobService(session, temporal=adapter).submit(
                owner=owner, request=request
            )
            result = await run_guarded_upgrade(session, operator_authorized=True)
            assert result.published
        monkeypatch.setattr(
            container_jobs,
            "get_temporal_artifact_service",
            lambda db, **kwargs: TemporalArtifactService(
                TemporalArtifactRepository(db),
                store=LocalTemporalArtifactStore(tmp_path / "job-evidence"),
                **kwargs,
            ),
        )
        monkeypatch.setattr(
            container_jobs,
            "ContainerJobService",
            lambda session, **kwargs: ContainerJobService(
                session, temporal=adapter, **kwargs
            ),
        )

        async def sessions():
            async with pg_maker() as session:
                yield session

        # Separate apps and database sessions retain only durable state.
        for attempt in (1, 2):
            app = FastAPI()
            app.include_router(container_jobs.router)
            app.dependency_overrides[container_jobs.get_async_session] = sessions
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                replay = await client.post(
                    "/api/v1/container-jobs",
                    json=request.model_dump(mode="json", by_alias=True),
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["jobId"] == accepted.job_id
                canceled = await client.post(
                    f"/api/v1/container-jobs/{accepted.job_id}/cancel",
                    json={"idempotencyKey": "cancel-retained-running"},
                )
                assert canceled.status_code == 200, canceled.text
                assert canceled.json()["replayed"] is (attempt == 2)
            async with pg_maker() as session:
                job = await session.get(ContainerJobRecord, accepted.job_id)
                assert job.owner_id == owner.principal_id
                assert job.state == "canceling"
                assert job.cancel_idempotency_key == "cancel-retained-running"
        handle = temporal.client.get_workflow_handle(
            container_job_workflow_id(accepted.job_id)
        )
        history = await handle.fetch_history()
        started = [
            e
            for e in history.events
            if e.HasField("workflow_execution_started_event_attributes")
        ]
        signals = [
            e
            for e in history.events
            if e.HasField("workflow_execution_signaled_event_attributes")
        ]
        assert len(started) == 1 and len(signals) == 1
        original = (
            await temporal.client.data_converter.decode(
                started[0].workflow_execution_started_event_attributes.input.payloads
            )
        )[0]
        assert original["jobId"] == accepted.job_id
        assert original["owner"] == owner.model_dump(by_alias=True)
        assert (
            signals[0].workflow_execution_signaled_event_attributes.signal_name
            == "cancel"
        )
