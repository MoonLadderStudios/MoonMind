"""The dependency migration preserves retained rows and owner FK semantics."""

import importlib
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import IntegrityError

from api_service.db.models import Base
from tests.support.isolated_postgres import isolated_postgres

pytestmark = pytest.mark.asyncio


def previous_tables():
    metadata = MetaData()
    artifacts = Base.metadata.tables["temporal_artifacts"].to_metadata(metadata)
    schedules = Base.metadata.tables["recurring_workflow_definitions"].to_metadata(
        metadata
    )
    for name in (
        "dependency_closure_version",
        "dependency_closure_sha256",
        "dependency_request_sha256",
    ):
        artifacts._columns.remove(artifacts.c[name])
    pointer = schedules.c.input_artifact_root_id
    for constraint in list(schedules.constraints):
        if pointer in list(constraint.columns):
            schedules.constraints.remove(constraint)
    for index in list(schedules.indexes):
        if pointer in list(index.columns):
            schedules.indexes.remove(index)
    schedules._columns.remove(pointer)
    return metadata, artifacts, schedules


async def test_dependency_migration_round_trip_preserves_data_and_root_lifetime():
    migration = importlib.import_module(
        "api_service.migrations.versions.406_verified_artifact_dependencies"
    )
    old, artifacts, schedules = previous_tables()
    async with isolated_postgres([]) as sessions, sessions() as db:
        connection = await db.connection()
        await connection.run_sync(old.create_all)
        await db.execute(
            artifacts.insert().values(artifact_id="art_root", storage_key="root")
        )
        await db.execute(
            artifacts.insert().values(
                artifact_id="art_retained", storage_key="retained"
            )
        )
        schedule_id = uuid4()
        await db.execute(
            schedules.insert().values(
                id=schedule_id, name="saved", cron="0 * * * *", timezone="UTC"
            )
        )

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        assert (
            await db.execute(
                text(
                    "SELECT dependency_closure_version FROM temporal_artifacts WHERE artifact_id = :id"
                ),
                {"id": "art_root"},
            )
        ).scalar_one() is None
        await db.execute(
            text(
                "INSERT INTO temporal_artifact_dependencies (root_artifact_id, dependency_artifact_id, role, sha256, required) VALUES (:root, :child, :role, NULL, false)"
            ),
            {"root": "art_root", "child": "art_optional_absent", "role": "evidence"},
        )
        await db.execute(
            text(
                "UPDATE recurring_workflow_definitions SET input_artifact_root_id = :root WHERE id = :id"
            ),
            {"root": "art_root", "id": schedule_id},
        )
        savepoint = await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text("DELETE FROM temporal_artifacts WHERE artifact_id = :id"),
                {"id": "art_root"},
            )
        await savepoint.rollback()
        await db.execute(
            text("DELETE FROM recurring_workflow_definitions WHERE id = :id"),
            {"id": schedule_id},
        )
        await db.execute(
            text("DELETE FROM temporal_artifacts WHERE artifact_id = :id"),
            {"id": "art_root"},
        )
        assert (
            await db.execute(
                text("SELECT count(*) FROM temporal_artifact_dependencies")
            )
        ).scalar_one() == 0

        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
            assert (
                "temporal_artifact_dependencies" not in inspect(sync).get_table_names()
            )
            assert "dependency_request_sha256" not in {
                column["name"]
                for column in inspect(sync).get_columns("temporal_artifacts")
            }

        await connection.run_sync(downgrade)
        assert (await db.execute(select(artifacts.c.artifact_id))).scalars().all() == [
            "art_retained"
        ]
        await connection.run_sync(upgrade)
        await db.commit()
