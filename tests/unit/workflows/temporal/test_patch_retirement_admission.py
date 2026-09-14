"""Catalog and admission-gate coverage for patch retirement.

MoonLadderStudios/MoonMind#3944.

Covers REQ-1 (durable per-patch catalog), REQ-3 (admission wiring through
the real ``TemporalClientAdapter.start_workflow`` path), and AC-5 (every
catalog entry carries checkable removal conditions).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.temporal import patch_retirement
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.patch_retirement import (
    CLASSIFICATION_DEAD_BRANCH,
    CLASSIFICATION_RETAINED_HISTORY,
    CLASSIFICATION_UNKNOWN,
    AuditEvidence,
    RetirementAdmissionBlocked,
)

# Captured at import time, before the autouse Temporal guard in conftest.py
# replaces ``start_workflow`` with a no-op. The wired-path tests below
# restore this so they exercise the real production admission method,
# including the retirement guard.
_REAL_START_WORKFLOW = TemporalClientAdapter.start_workflow

REPO_ROOT = Path(__file__).resolve().parents[4]


def _healthy_evidence(**overrides: Any) -> AuditEvidence:
    kwargs: dict[str, Any] = {
        "deployment_versions": ("2026.09.10", "2026.09.12"),
        "admission_cutoff": "2026-09-12T00:00:00Z",
        "reset_replay_supported": False,
    }
    kwargs.update(overrides)
    return AuditEvidence(**kwargs)


# --- Catalog (REQ-1, AC-5) ---------------------------------------------------


def test_catalog_entries_carry_both_stage_conditions() -> None:
    """AC-5: every cataloged patch has concrete, checkable conditions."""
    assert patch_retirement.PATCH_CATALOG
    for entry in patch_retirement.PATCH_CATALOG:
        assert entry.deprecate_condition.strip(), entry.patch_id
        assert entry.remove_condition.strip(), entry.patch_id
        assert entry.command_boundary.strip(), entry.patch_id
        assert entry.old_behavior.strip(), entry.patch_id
        assert entry.new_behavior.strip(), entry.patch_id
        assert entry.fixture.strip(), entry.patch_id
        assert entry.classification in {
            CLASSIFICATION_DEAD_BRANCH,
            CLASSIFICATION_RETAINED_HISTORY,
            CLASSIFICATION_UNKNOWN,
        }, entry.patch_id


def test_catalog_entries_resolve_in_static_inventory() -> None:
    """REQ-1: catalog records describe patches the inventory can find."""
    records = patch_retirement.inventory_patches(REPO_ROOT)
    inventoried = {record.patch_id for record in records}
    for entry in patch_retirement.PATCH_CATALOG:
        assert entry.patch_id in inventoried, entry.patch_id


def test_catalog_lookup_misses_return_none() -> None:
    assert patch_retirement.catalog_entry("no-such-patch-v1") is None
    assert (
        patch_retirement.catalog_entry("run-conditional-registry-read-v1")
        is not None
    )


def test_catalog_coverage_counts_distinct_ids() -> None:
    records = patch_retirement.inventory_patches(REPO_ROOT)
    coverage = patch_retirement.catalog_coverage(records)
    assert coverage["cataloged"] == len(patch_retirement.PATCH_CATALOG)
    assert coverage["cataloged"] + coverage["uncatalogued"] == len(
        {record.patch_id for record in records}
    )


# --- Admission decision (REQ-3) ----------------------------------------------


def test_admission_blocked_on_unknown_evidence() -> None:
    check = patch_retirement.check_retirement_admission(
        ["run-example-v1"],
        _healthy_evidence(visibility_failures=("visibility query timed out",)),
    )
    assert check.allowed is False
    assert any("run-example-v1" in reason for reason in check.reasons)


def test_admission_allowed_with_known_consumers() -> None:
    """route_new_only still admits: versioning routes new work to new code."""
    check = patch_retirement.check_retirement_admission(
        ["run-example-v1"],
        _healthy_evidence(retained_markers=frozenset({"run-example-v1"})),
    )
    assert check.allowed is True
    assert check.reasons


def test_admission_allowed_with_no_consumers() -> None:
    check = patch_retirement.check_retirement_admission(
        ["run-example-v1"], _healthy_evidence()
    )
    assert check.allowed is True


def test_admission_allowed_with_empty_patch_set() -> None:
    check = patch_retirement.check_retirement_admission([], _healthy_evidence())
    assert check.allowed is True


# --- Wired admission path (REQ-3 boundary) ------------------------------------


def _adapter_with_mock_client() -> tuple[TemporalClientAdapter, AsyncMock]:
    client = AsyncMock()
    client.start_workflow = AsyncMock(
        return_value=SimpleNamespace(id="wf-id", result_run_id="run-id")
    )
    return TemporalClientAdapter(client=client), client


@pytest.mark.asyncio
async def test_wired_admission_holds_start_on_unknown_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real start path holds new executions while evidence is unknown."""
    monkeypatch.setattr(
        TemporalClientAdapter, "start_workflow", _REAL_START_WORKFLOW
    )
    adapter, client = _adapter_with_mock_client()
    with pytest.raises(RetirementAdmissionBlocked):
        await adapter.start_workflow(
            workflow_type="MoonMind.Run",
            workflow_id="mm:admission-held",
            task_queue="mm.workflow",
            retirement_evidence=_healthy_evidence(
                visibility_failures=("visibility query timed out",),
                history_failures=("history fetch unavailable",),
            ),
            retirement_patch_ids=("fetch-profile-snapshots-v1",),
        )
    client.start_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_wired_admission_starts_with_known_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known consumers do not stop new work: versioning routes it forward."""
    monkeypatch.setattr(
        TemporalClientAdapter, "start_workflow", _REAL_START_WORKFLOW
    )
    adapter, client = _adapter_with_mock_client()
    result = await adapter.start_workflow(
        workflow_type="MoonMind.Run",
        workflow_id="mm:admission-routed",
        task_queue="mm.workflow",
        retirement_evidence=_healthy_evidence(
            retained_markers=frozenset({"fetch-profile-snapshots-v1"})
        ),
        retirement_patch_ids=("fetch-profile-snapshots-v1",),
    )
    assert result.workflow_id == "wf-id"
    client.start_workflow.assert_called_once()


@pytest.mark.asyncio
async def test_wired_admission_default_preserves_current_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No guard supplied: the wired path starts exactly as before."""
    monkeypatch.setattr(
        TemporalClientAdapter, "start_workflow", _REAL_START_WORKFLOW
    )
    adapter, client = _adapter_with_mock_client()
    result = await adapter.start_workflow(
        workflow_type="MoonMind.Run",
        workflow_id="mm:admission-default",
        task_queue="mm.workflow",
    )
    assert result.workflow_id == "wf-id"
    client.start_workflow.assert_called_once()
