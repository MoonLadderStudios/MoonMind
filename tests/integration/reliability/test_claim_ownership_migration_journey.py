"""Retiring a reservation must not wait for its bookkeeping, in real SQL.

The partial unique index is what actually decides whether a successor can
reserve an issue locally, so the migration that changes its predicate is
exercised against a populated PostgreSQL database, including the downgrade
path that has no column to represent a retired-but-unfinished reservation.
"""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_INSERT = (
    "INSERT INTO github_issue_claims "
    "(owner, repository, issue_number, attempt_id, actor_id, comment_body) "
    "VALUES (:owner, :repository, :issue_number, :attempt_id, 'actor', 'body')"
)


def _apply(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


async def test_ownership_end_frees_the_issue_while_bookkeeping_is_retained():
    created = importlib.import_module(
        "api_service.migrations.versions.379_durable_issue_claims"
    )
    ownership = importlib.import_module(
        "api_service.migrations.versions.382_issue_claim_ownership_end"
    )
    bindings = sa.Table(
        "omnigent_runtime_bindings",
        sa.MetaData(),
        sa.Column("binding_id", sa.Text(), primary_key=True),
    )
    async with isolated_postgres([bindings]) as sessions:
        engine = sessions.kw["bind"]
        async with engine.begin() as connection:
            await connection.run_sync(_apply, created.upgrade)
            await connection.execute(
                sa.text(_INSERT),
                {
                    "owner": "default/predecessor",
                    "repository": "example/repo",
                    "issue_number": 4271,
                    "attempt_id": "att-predecessor",
                },
            )
            await connection.run_sync(_apply, ownership.upgrade)

            # The populated reservation is preserved and still reserving.
            row = (
                await connection.execute(
                    sa.text(
                        "SELECT released, ownership_ended, comment_body "
                        "FROM github_issue_claims WHERE owner = 'default/predecessor'"
                    )
                )
            ).mappings().one()
            assert row["ownership_ended"] is False
            assert row["released"] is False
            assert row["comment_body"] == "body"

        # A successor cannot reserve the same issue while ownership stands.
        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    sa.text(_INSERT),
                    {
                        "owner": "other/successor",
                        "repository": "example/repo",
                        "issue_number": 4271,
                        "attempt_id": "att-successor",
                    },
                )

        # Retiring the reservation frees the issue even though the terminal
        # bookkeeping (released) is still outstanding.
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE github_issue_claims SET ownership_ended = true, "
                    "ownership_ended_reason = 'mutation_denied' "
                    "WHERE owner = 'default/predecessor'"
                )
            )
            await connection.execute(
                sa.text(_INSERT),
                {
                    "owner": "other/successor",
                    "repository": "example/repo",
                    "issue_number": 4271,
                    "attempt_id": "att-successor",
                },
            )
            retained = (
                await connection.execute(
                    sa.text(
                        "SELECT released, ownership_ended_reason FROM "
                        "github_issue_claims WHERE owner = 'default/predecessor'"
                    )
                )
            ).mappings().one()
            # The unfinished bookkeeping is retained for a later retry.
            assert retained["released"] is False
            assert retained["ownership_ended_reason"] == "mutation_denied"

        # The downgrade has no way to represent "retired but unfinished", so
        # it releases those rows rather than violating the old invariant.
        async with engine.begin() as connection:
            await connection.run_sync(_apply, ownership.downgrade)
            rows = (
                (
                    await connection.execute(
                        sa.text(
                            "SELECT owner, released FROM github_issue_claims "
                            "ORDER BY owner"
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert [(row["owner"], row["released"]) for row in rows] == [
                ("default/predecessor", True),
                ("other/successor", False),
            ]
