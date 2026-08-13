from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest


def test_checkpoint_blob_dedup_downgrade_rejects_shared_keys_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.355_checkpoint_blob_dedup"
    )
    operations = MagicMock()
    operations.get_bind.return_value.execute.return_value.scalar_one.return_value = 1
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError, match="irreversible after shared checkpoint"):
        migration.downgrade()

    operations.drop_index.assert_not_called()
    operations.create_unique_constraint.assert_not_called()


def test_checkpoint_blob_dedup_downgrade_restores_unique_key_when_unshared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.355_checkpoint_blob_dedup"
    )
    operations = MagicMock()
    operations.get_bind.return_value.execute.return_value.scalar_one.return_value = 0
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    operations.drop_index.assert_called_once_with(
        "ix_temporal_artifacts_storage_key",
        table_name="temporal_artifacts",
    )
    operations.create_unique_constraint.assert_called_once_with(
        "temporal_artifacts_storage_key_key",
        "temporal_artifacts",
        ["storage_key"],
    )
