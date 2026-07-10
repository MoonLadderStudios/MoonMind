"""Regression coverage for the provider profile model tiers migration."""

from __future__ import annotations

import importlib
import json

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

from api_service.db.models import ManagedAgentProviderProfile


class _RecordingOp:
    def __init__(self) -> None:
        self.columns = []

    def add_column(self, table_name, column) -> None:
        self.columns.append((table_name, column))

    def get_bind(self):
        return self

    def execute(self, _statement):
        return []

    def create_check_constraint(self, *_args, **_kwargs) -> None:
        return None


def _assert_valid_model_tiers_default(column) -> None:
    default_sql = str(
        column.server_default.arg.compile(
            dialect=postgresql.dialect(),
        )
    )
    assert default_sql.startswith("'") and default_sql.endswith("'")
    assert json.loads(default_sql[1:-1]) == [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        }
    ]

    ddl = str(CreateColumn(column).compile(dialect=postgresql.dialect()))
    assert '"model":null' in ddl
    assert '"effort":null' in ddl


def test_model_tiers_server_default_compiles_as_valid_postgresql_json(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.335_provider_profile_model_tiers"
    )
    operations = _RecordingOp()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    model_tiers_column = next(
        column
        for table_name, column in operations.columns
        if table_name == "managed_agent_provider_profiles"
        and column.name == "model_tiers"
    )
    _assert_valid_model_tiers_default(model_tiers_column)


def test_model_tiers_model_default_compiles_as_valid_postgresql_json() -> None:
    _assert_valid_model_tiers_default(
        ManagedAgentProviderProfile.__table__.c.model_tiers
    )
