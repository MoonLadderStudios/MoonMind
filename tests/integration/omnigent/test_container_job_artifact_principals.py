"""Replay long Omnigent job owners through the PostgreSQL artifact boundary."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ContainerJobRecord,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
)
from api_service.services.container_jobs import (
    ContainerJobNotFoundError,
    ContainerJobService,
    owner_artifact_principal,
)
from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    OwnerIdentity,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_evidence_publisher

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_TABLES = [
    TemporalArtifact.__table__,
    TemporalArtifactLink.__table__,
    TemporalArtifactPin.__table__,
    ContainerJobRecord.__table__,
]
_FIXTURE = Path(__file__).with_name("fixtures") / "container_job_long_owner.json"


@pytest.mark.parametrize("principal_type", ["service", "system", "user"])
async def test_long_owner_publishes_and_reads_job_evidence(
    control_plane_postgres_url, tmp_path, principal_type, monkeypatch
):
    """A valid 255-character owner needs room for its artifact type prefix."""
    payload = json.loads(_FIXTURE.read_text())
    payload["owner"]["principalType"] = principal_type
    request = ContainerJobActivityRequest.model_validate(payload)
    original = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    principal = owner_artifact_principal(request.owner)
    assert len(request.owner.principal_id) == 255
    assert len(principal) > 255
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pytest-unit.xml").write_text('<testsuite tests="1" failures="0"/>')
    request.resolved_workspace_ref = str(workspace)
    engine = create_async_engine(control_plane_postgres_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(conn, tables=_TABLES)
            )
        async with maker() as session:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            jobs = ContainerJobService(session, artifacts=artifacts)
            record, _ = await jobs.repository.create_or_replay(
                owner=request.owner, request=request.request
            )
            await session.commit()
            request.job_id = record.job_id
            publisher = _container_job_evidence_publisher(artifacts)
            # This is the exact production publisher that refused the launch
            # attestation before any pytest process could start.
            launch_ref = await publisher(
                request,
                f"{record.job_id}-egress-attestation.json",
                b'{"stage":"created_unstarted"}',
            )

            async def daemon(args):
                assert args[0] == "logs"
                return 0, b"1 passed\n", b""

            backend = DockerContainerJobBackend(
                workspace_root=tmp_path,
                command_runner=daemon,
                evidence_publisher=publisher,
            )
            runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
            request.container_ref = "test-container"
            request.exit_code = 0
            request.terminal_state = "succeeded"
            result = await runtime.container_job_publish_evidence(
                request.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
            record.logs_ref = result["logsRef"]
            record.artifacts_ref = result["artifactsRef"]
            record.state = "succeeded"
            await session.commit()

        # Read through a separate API session, using the same owner identity.
        async with maker() as session:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            jobs = ContainerJobService(session, artifacts=artifacts)
            logs = await jobs.logs(owner=request.owner, job_id=request.job_id)
            assert any("1 passed" in line.text for line in logs.entries)
            outputs = await jobs.artifacts(owner=request.owner, job_id=request.job_id)
            assert outputs.artifacts[0].collection_status == "collected"
            pin = await artifacts.pin(
                artifact_id=launch_ref, principal=principal, reason="replay"
            )
            assert pin.pinned_by_principal == principal
            rows = (await session.scalars(select(TemporalArtifact))).all()
            assert rows and all(row.created_by_principal == principal for row in rows)
            # Distinct owners sharing a long prefix remain distinct.
            foreign = OwnerIdentity(
                principalType=principal_type,
                principalId=request.owner.principal_id[:-1] + "x",
            )
            with pytest.raises(ContainerJobNotFoundError):
                await jobs.logs(owner=foreign, job_id=request.job_id)
            assert original["owner"] == payload["owner"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "oversized_table", ["temporal_artifacts", "temporal_artifact_pins"]
)
async def test_principal_migration_preserves_rows_and_rejects_lossy_downgrade(
    control_plane_postgres_url, monkeypatch, oversized_table
):
    migration = import_module(
        "api_service.migrations.versions.375_artifact_principal_text"
    )
    engine = create_async_engine(control_plane_postgres_url)

    def exercise(connection):
        metadata = sa.MetaData()
        tables = {}
        for name, column, nullable in migration._COLUMNS:
            tables[name] = sa.Table(
                name,
                metadata,
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column(column, sa.String(255), nullable=nullable),
            )
        metadata.create_all(connection)
        for name, column, _nullable in migration._COLUMNS:
            connection.execute(
                tables[name].insert().values(id=1, **{column: "user:legacy"})
            )
        connection.execute(
            tables["temporal_artifacts"]
            .insert()
            .values(id=2, created_by_principal=None)
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        for name, column, _nullable in migration._COLUMNS:
            assert (
                connection.scalar(
                    sa.select(tables[name].c[column]).where(tables[name].c.id == 1)
                )
                == "user:legacy"
            )
            assert isinstance(
                sa.inspect(connection).get_columns(name)[1]["type"], sa.Text
            )
        artifacts = tables["temporal_artifacts"]
        assert (
            connection.scalar(
                sa.select(artifacts.c.created_by_principal).where(artifacts.c.id == 2)
            )
            is None
        )
        column = next(
            column
            for name, column, _nullable in migration._COLUMNS
            if name == oversized_table
        )
        oversized = tables[oversized_table]
        long_principal = "service:" + "x" * 255
        connection.execute(oversized.insert().values(id=3, **{column: long_principal}))
        with pytest.raises(RuntimeError, match="Cannot downgrade artifact principals"):
            migration.downgrade()
        # Both columns must still be wide even when only the second table
        # contains a long principal. No partial schema rollback is allowed.
        for name, _column, _nullable in migration._COLUMNS:
            assert isinstance(
                sa.inspect(connection).get_columns(name)[1]["type"], sa.Text
            )
        assert (
            connection.scalar(sa.select(oversized.c[column]).where(oversized.c.id == 3))
            == long_principal
        )
        connection.execute(oversized.delete().where(oversized.c.id == 3))
        migration.downgrade()
        for name, _column, _nullable in migration._COLUMNS:
            assert sa.inspect(connection).get_columns(name)[1]["type"].length == 255
        migration.upgrade()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(exercise)
    finally:
        await engine.dispose()
