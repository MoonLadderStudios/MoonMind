"""Regression tests for the MM-912 preset slug/scope migration."""

from __future__ import annotations

import importlib
from typing import Any


class RecordingOp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __getattr__(self, name: str):
        def recorder(*args, **kwargs):
            self.calls.append((name, args[0] if args else kwargs))

        return recorder


def test_preset_recents_are_deduplicated_before_template_unique_constraint(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.327_mm912_slug_scope_presets"
    )
    op = RecordingOp()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    duplicate_delete_index = next(
        index
        for index, (name, payload) in enumerate(op.calls)
        if name == "execute"
        and "delete from preset_recents" in str(payload)
        and "partition by user_id, template_id" in str(payload)
    )
    unique_constraint_index = next(
        index
        for index, (name, payload) in enumerate(op.calls)
        if name == "create_unique_constraint"
        and payload == "uq_preset_recent_user_template"
    )

    assert duplicate_delete_index < unique_constraint_index
