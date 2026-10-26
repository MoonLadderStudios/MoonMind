"""The packaged database initializer must select an installed sync driver."""

import pytest
from sqlalchemy import create_engine

from tests.unit.config.test_container_backend_compose_declarations import (
    _interpolate,
    _service_environment,
)


@pytest.mark.parametrize(
    "overrides",
    (
        {},
        {
            "POSTGRES_USER": "fixture",
            "POSTGRES_PASSWORD": "fixture_password",
            "POSTGRES_HOST": "fixture.invalid",
            "POSTGRES_DB": "fixture_database",
        },
    ),
)
def test_initializer_database_url_constructs_an_installed_driver(
    overrides: dict[str, str],
) -> None:
    url = _interpolate(_service_environment("init-db")["DATABASE_URL"], overrides)
    # This is the initializer's actual engine construction boundary. It imports
    # the selected DBAPI without opening a database connection; SQLAlchemy 2.1's
    # implicit default fails here when only the supported psycopg2 is installed.
    engine = create_engine(url)
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.dialect.loaded_dbapi.__name__ == "psycopg2"
        if overrides:
            assert engine.url.username == overrides["POSTGRES_USER"]
            assert engine.url.password == overrides["POSTGRES_PASSWORD"]
            assert engine.url.host == overrides["POSTGRES_HOST"]
            assert engine.url.database == overrides["POSTGRES_DB"]
    finally:
        engine.dispose()
