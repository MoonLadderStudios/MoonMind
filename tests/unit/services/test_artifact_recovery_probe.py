"""Admission probes stay indexed while observing new recovery work immediately."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, insert, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api_service.db.models import TemporalArtifact
from api_service.services import single_user_conversion as conversion

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def artifact_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(TemporalArtifact.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize("auth_provider", ["disabled", "oidc"])
async def test_negative_admission_probe_does_not_walk_artifact_history(
    artifact_database, monkeypatch, auth_provider
):
    monkeypatch.setenv("AUTH_PROVIDER", auth_provider)
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    captured = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if statement.startswith("SELECT temporal_artifacts.artifact_id"):
            captured.append((statement, parameters))

    async with AsyncSession(artifact_database) as session:
        await session.execute(
            insert(TemporalArtifact),
            [
                {
                    "artifact_id": f"history-{number}",
                    "storage_key": f"history-{number}",
                    "created_by_principal": "operator",
                }
                for number in range(200)
            ],
        )
        await session.commit()
        event.listen(artifact_database.sync_engine, "before_cursor_execute", capture)
        try:
            assert not await conversion._has_pending_artifact_recovery(session)
        finally:
            event.remove(
                artifact_database.sync_engine, "before_cursor_execute", capture
            )
        statement, parameters = captured.pop()
        connection = await session.connection()
        plan = (
            await connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + statement, parameters
            )
        ).all()
        assert any(
            "USING INDEX ix_temporal_artifacts_recovery_owner" in row[-1]
            for row in plan
        ), plan


@pytest.mark.parametrize(
    ("principal", "pending"),
    [
        ("operator", False),
        ("\t system\n", False),
        ("agent_workflow", False),
        ("agent_runtime", False),
        ("service:journal", False),
        ("\u2003workflow:run\t", False),
        ("system:maintenance", False),
        ("workflow:", True),
        ("service:\t\u2003", True),
        ("Workflow:run", True),
        ("SYSTEM", True),
        (None, True),
        ("", True),
        ("unknown-owner", True),
        ("11111111-1111-4111-8111-111111111111", True),
        ("user:11111111-1111-4111-8111-111111111111", True),
    ],
)
async def test_recovery_probe_preserves_machine_and_ambiguous_owner_boundaries(
    artifact_database, monkeypatch, principal, pending
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with AsyncSession(artifact_database) as session:
        session.add(
            TemporalArtifact(
                artifact_id="evidence",
                storage_key="evidence",
                created_by_principal=principal,
            )
        )
        await session.commit()
        assert await conversion._has_pending_artifact_recovery(session) is pending


@pytest.mark.parametrize("auth_provider", ["disabled", "oidc"])
@pytest.mark.parametrize("prefix", ["", "user:"])
async def test_reserved_scope_is_only_exempt_for_the_unchanged_disabled_scope(
    artifact_database, monkeypatch, auth_provider, prefix
):
    monkeypatch.setenv("AUTH_PROVIDER", auth_provider)
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    async with AsyncSession(artifact_database) as session:
        session.add(
            TemporalArtifact(
                artifact_id="local",
                storage_key="local",
                created_by_principal=prefix + conversion.DEPLOYMENT_SUBJECT_ID,
            )
        )
        await session.commit()
        assert await conversion._has_pending_artifact_recovery(session) is (
            auth_provider != "disabled"
        )
        monkeypatch.setenv("DEFAULT_USER_ID", str(uuid4()))
        assert await conversion._has_pending_artifact_recovery(session)


async def test_recovery_probe_observes_insert_owner_change_and_revoked_grant(
    artifact_database, monkeypatch
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with AsyncSession(artifact_database) as session:
        assert not await conversion._has_pending_artifact_recovery(session)
        session.add(
            TemporalArtifact(
                artifact_id="later",
                storage_key="later",
                created_by_principal=str(uuid4()),
            )
        )
        await session.commit()
        assert await conversion._has_pending_artifact_recovery(session)
        await session.execute(
            update(TemporalArtifact).values(operator_recovery_granted=True)
        )
        await session.commit()
    async with AsyncSession(artifact_database) as restarted:
        assert not await conversion._has_pending_artifact_recovery(restarted)
        await restarted.execute(
            update(TemporalArtifact).values(operator_recovery_granted=False)
        )
        await restarted.commit()
        assert await conversion._has_pending_artifact_recovery(restarted)
        await restarted.execute(
            update(TemporalArtifact).values(created_by_principal="service:runtime")
        )
        await restarted.commit()
        assert not await conversion._has_pending_artifact_recovery(restarted)
        await restarted.execute(
            update(TemporalArtifact).values(created_by_principal=None)
        )
        await restarted.commit()
        assert await conversion._has_pending_artifact_recovery(restarted)
