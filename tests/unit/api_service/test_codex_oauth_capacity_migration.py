from __future__ import annotations

import importlib
class _Bind:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement: object) -> None:
        self.statements.append(statement)


class _Operations:
    def __init__(self) -> None:
        self.bind = _Bind()
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []

    def get_bind(self) -> _Bind:
        return self.bind

    def create_check_constraint(self, name: str, table: str, condition: str) -> None:
        self.created_constraints.append((name, table, condition))

    def drop_constraint(self, name: str, table: str, *, type_: str) -> None:
        self.dropped_constraints.append((name, table, type_))


def test_migration_repairs_and_constrains_codex_oauth_capacity(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.336_codex_oauth_exclusive_capacity"
    )
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert len(operations.bind.statements) == 1
    rendered = str(operations.bind.statements[0])
    assert "runtime_id =" in rendered
    assert "credential_source =" in rendered
    assert "runtime_materialization_mode =" in rendered
    assert "max_parallel_runs" in rendered
    assert operations.created_constraints == [
        (
            "ck_provider_profiles_codex_oauth_exclusive_capacity",
            "managed_agent_provider_profiles",
            migration.CODEX_OAUTH_EXCLUSIVE_CAPACITY_CHECK,
        )
    ]

    migration.downgrade()

    assert operations.dropped_constraints == [
        (
            "ck_provider_profiles_codex_oauth_exclusive_capacity",
            "managed_agent_provider_profiles",
            "check",
        )
    ]
