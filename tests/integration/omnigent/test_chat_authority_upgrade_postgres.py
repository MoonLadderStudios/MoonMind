"""Verify the ownership data repair using PostgreSQL locking and JSON values."""

from uuid import uuid4

import pytest
import sqlalchemy as sa

from api_service.db.models import Base, OmnigentSession, OmnigentTurnAttempt
from tests.unit.omnigent.test_chat_authority_upgrade import _row, _seed, _upgrade

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def connection(control_plane_postgres_url):
    engine = sa.create_engine(
        control_plane_postgres_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = "chat_upgrade_" + uuid4().hex
            connection.execute(sa.schema.CreateSchema(schema))
            connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
            Base.metadata.create_all(connection)
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.mark.parametrize(
    "fault", [None, "owner", "provider", "revision", "fence", "reason"]
)
def test_upgrade_preserves_authority_on_postgres(connection, monkeypatch, fault):
    _seed(connection, fault=fault)
    original_turn = dict(_row(connection, OmnigentTurnAttempt))
    migration = _upgrade(connection, monkeypatch)
    session = dict(_row(connection, OmnigentSession))
    assert session["historical_read_state"] == (
        "live" if fault is None else "quarantined"
    )
    assert dict(_row(connection, OmnigentTurnAttempt)) == original_turn
    migration.upgrade()
    assert dict(_row(connection, OmnigentSession)) == session
