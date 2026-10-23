"""GH4354: historical Alembic, populated conversion and real API process restart."""

import asyncio
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
from api_service.db.models import ManagedSecret, TemporalArtifact, WorkflowRun
from api_service.services.single_user_conversion import SingleUserConversionRun
from tests.helpers.account_source import User, UserProfile
from tests.integration import test_single_user_conversion_postgres_4346 as source_pg

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
                session.add(
                    TemporalArtifact(
                        artifact_id="retained",
                        storage_key="unchanged-key",
                        created_by_principal=str(owners[0]),
                    )
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
        seed.add(User(id=owner, email="retained-reader@example.invalid"))
        seed.add(UserProfile(user_id=owner, openai_api_key_encrypted="reader-test-key"))
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
        await retry.rollback()
        assert (await run_guarded_upgrade(retry, operator_authorized=True)).published
