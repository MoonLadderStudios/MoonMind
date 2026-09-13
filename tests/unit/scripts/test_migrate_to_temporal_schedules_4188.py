"""Retired manifest_run disposition in the Temporal schedule migration (#4188).

MoonLadderStudios/MoonMind#4188 (MR1): the native Manifest product is
retired. ``scripts/migrate_to_temporal_schedules.py`` must never convert a
``manifest_run`` definition into an ordinary run and must never create a
Temporal Schedule for the retired ``MoonMind.ManifestIngest`` type. Retired
definitions are skipped with protected evidence left to the
retirement path (pause/remove), so the migration performs no Temporal or
database side effect for them.

The migration module imports heavy runtime dependencies (SQLAlchemy,
Temporal client, app settings) that are unavailable in a bare unit
interpreter. These tests exec only the pure target-mapping section of the
script source, which keeps the hermetic boundary honest: the test proves
the exact shipped logic raises before any workflow-type string is
produced, and that the loop skips retired definitions before
``adapter.create_schedule``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_to_temporal_schedules.py"


def _load_target_mapping_namespace() -> dict:
    """Exec the pure target-mapping section of the migration script."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^class RetiredManifestTargetError.*?(?=^async def migrate_definitions)",
        source,
    )
    assert match is not None, "retired-target mapping block missing from script"
    namespace: dict = {}
    exec(compile(match.group(0), str(SCRIPT_PATH), "exec"), namespace)
    return namespace


def test_manifest_run_target_raises_retired_error() -> None:
    """A legacy manifest_run target raises instead of mapping to a type."""
    ns = _load_target_mapping_namespace()
    with pytest.raises(ns["RetiredManifestTargetError"], match="retired"):
        ns["_workflow_type_for_target"]({"kind": "manifest_run"})


def test_ordinary_targets_still_map_to_run() -> None:
    """Ordinary queue targets keep migrating; nothing is silently dropped."""
    ns = _load_target_mapping_namespace()
    assert ns["_workflow_type_for_target"]({"kind": "queue_task"}) == "MoonMind.UserWorkflow"
    assert (
        ns["_workflow_type_for_target"]({"kind": "queue_task_template"})
        == "MoonMind.UserWorkflow"
    )
    assert ns["_workflow_type_for_target"]({"kind": "other"}) == "MoonMind.UserWorkflow"


def test_retired_definitions_skip_before_schedule_creation() -> None:
    """The loop skips retired definitions before any Temporal/DB effect."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    # The retired-target guard must precede adapter.create_schedule so
    # rejection happens before Temporal start or temporal_schedule_id
    # mutation, and must never map to the retired workflow type.
    assert "RetiredManifestTargetError" in source
    assert "Skipping retired manifest_run definition" in source
    assert "return \"MoonMind.ManifestIngest\"" not in source
    guard_pos = source.index("except RetiredManifestTargetError")
    create_pos = source.index("adapter.create_schedule")
    assert guard_pos < create_pos
    assert "continue" in source[guard_pos:create_pos]


def test_producer_ledger_covers_migration_disposition() -> None:
    """The MR1 caller ledger maps the migration path to this test."""
    ledger = (
        REPO_ROOT / "docs" / "tmp" / "ManifestProducerLedger-4188.md"
    ).read_text(encoding="utf-8")
    assert "migrate_to_temporal_schedules" in ledger
    assert "manifest_run" in ledger
    assert "test_migrate_to_temporal_schedules_4188" in ledger
