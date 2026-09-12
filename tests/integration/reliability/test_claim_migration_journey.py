"""Additive migration preserves old bindings and refuses to erase receipts."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_claim_migration_preserves_populated_bindings_and_guards_downgrade():
    migration = importlib.import_module(
        "api_service.migrations.versions.379_durable_issue_claims"
    )
    metadata = sa.MetaData()
    old_bindings = sa.Table(
        "omnigent_runtime_bindings",
        metadata,
        sa.Column("binding_id", sa.Text(), primary_key=True),
        sa.Column("terminal_result_json", sa.JSON()),
        sa.Column("provider_leases_json", sa.JSON(), nullable=False),
    )
    prior = {
        "binding_id": "retained-v2",
        "terminal_result_json": {"summary": "primary completed"},
        "provider_leases_json": {"primary-model": {"credentialGeneration": 4}},
    }

    def apply(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    async with isolated_postgres([old_bindings]) as sessions:
        async with sessions() as session:
            await session.execute(old_bindings.insert().values(**prior))
            await session.commit()
        engine = sessions.kw["bind"]
        async with engine.begin() as connection:
            await connection.run_sync(apply, migration.upgrade)
            assert (
                dict(
                    (await connection.execute(sa.select(old_bindings))).mappings().one()
                )
                == prior
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO github_issue_claims (owner, repository, issue_number, attempt_id, actor_id, comment_body) "
                    "VALUES ('namespace/workflow', 'example/repo', 1, 'attempt', 'actor', 'owned claim')"
                )
            )
            with pytest.raises(RuntimeError, match="Retained issue claims"):
                await connection.run_sync(apply, migration.downgrade)
            # Fixture retention expiry, in this isolated schema only.
            await connection.execute(sa.text("DELETE FROM github_issue_claims"))
            await connection.execute(
                sa.text(
                    'UPDATE omnigent_runtime_bindings SET phase_results_json = \'{"publication": {"summary": "complete"}}\''
                )
            )
            with pytest.raises(RuntimeError, match="Runtime phase receipts"):
                await connection.run_sync(apply, migration.downgrade)
            await connection.execute(
                sa.text(
                    "UPDATE omnigent_runtime_bindings SET phase_results_json = NULL"
                )
            )
            await connection.run_sync(apply, migration.downgrade)
            assert (
                dict(
                    (await connection.execute(sa.select(old_bindings))).mappings().one()
                )
                == prior
            )
            await connection.run_sync(apply, migration.upgrade)
            assert (
                dict(
                    (await connection.execute(sa.select(old_bindings))).mappings().one()
                )
                == prior
            )
