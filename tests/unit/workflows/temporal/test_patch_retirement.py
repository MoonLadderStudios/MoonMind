"""Audit, gating, inventory, and replay coverage for patch retirement.

MoonLadderStudios/MoonMind#3944.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import temporalio.workflow as temporal_workflow_module
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal import patch_retirement
from moonmind.workflows.temporal.patch_retirement import (
    AuditEvidence,
    PatchRecord,
)

# Captured at import time, before the autouse no-op deprecate_patch fixture
# in conftest.py replaces the attribute for direct-method unit tests. The
# replay test below restores this so histories execute against genuine SDK
# marker semantics.
_REAL_DEPRECATE_PATCH = temporal_workflow_module.deprecate_patch

REPO_ROOT = Path(__file__).resolve().parents[4]
CONDITIONAL_REGISTRY_PATCH = "run-conditional-registry-read-v1"


def _record(patch_id: str = "run-example-v1") -> PatchRecord:
    return PatchRecord(
        patch_id=patch_id,
        constant_name="RUN_EXAMPLE_PATCH",
        file="moonmind/workflows/temporal/workflows/run.py",
        line=1,
        workflow_type="MoonMindRunWorkflow",
        usage_kind=patch_retirement.USAGE_BRANCH,
    )


def _healthy_evidence(**overrides) -> AuditEvidence:
    kwargs: dict = {
        "deployment_versions": ("2026.09.10", "2026.09.12"),
        "admission_cutoff": "2026-09-12T00:00:00Z",
        "reset_replay_supported": False,
    }
    kwargs.update(overrides)
    return AuditEvidence(**kwargs)


# --- Inventory (REQ-1, AC-5) -------------------------------------------------


def test_inventory_finds_known_patches_with_usage_kinds() -> None:
    records = patch_retirement.inventory_patches(REPO_ROOT)
    by_id: dict[str, list[PatchRecord]] = {}
    for record in records:
        by_id.setdefault(record.patch_id, []).append(record)
    assert CONDITIONAL_REGISTRY_PATCH in by_id
    assert "run-workflow-nested-propose-tasks" in by_id
    assert "refactor-loop-1.2" in by_id
    # Literal ids are inventoried, not skipped.
    assert "run-materialize-evidence-retry-v1" in by_id
    # Constants from every workflow file resolve to string ids.
    assert "agent-run-accurate-slot-wait-reason-v1" in by_id


def test_retired_marker_no_longer_a_live_patched_branch() -> None:
    """AC-5: the batch-1 retirement measurably decreases live patch debt."""
    records = patch_retirement.inventory_patches(REPO_ROOT)
    usages = {
        record.usage_kind
        for record in records
        if record.patch_id == CONDITIONAL_REGISTRY_PATCH
    }
    assert usages == {patch_retirement.USAGE_DEPRECATED}


def test_inventory_skips_dynamic_dispatch_pseudo_ids() -> None:
    records = patch_retirement.inventory_patches(REPO_ROOT)
    assert not [r for r in records if r.patch_id == "patch_id"]


# --- Audit verdicts (REQ-2, AC-1, AC-3) --------------------------------------


def test_healthy_evidence_with_no_consumers_allows_deprecation() -> None:
    finding = patch_retirement.audit_patch(
        _record(), _healthy_evidence(), stage=patch_retirement.STAGE_DEPRECATE
    )
    assert finding.verdict == patch_retirement.VERDICT_SAFE_TO_DEPRECATE


def test_removal_blocked_by_reset_replay_policy_despite_no_markers() -> None:
    finding = patch_retirement.audit_patch(
        _record(),
        _healthy_evidence(reset_replay_supported=True),
        stage=patch_retirement.STAGE_REMOVE,
    )
    assert finding.verdict == patch_retirement.VERDICT_REQUIRES_COMPATIBILITY
    assert any("reset/replay" in reason for reason in finding.reasons)


def test_removal_allowed_once_reset_replay_exposure_cleared() -> None:
    finding = patch_retirement.audit_patch(
        _record(), _healthy_evidence(), stage=patch_retirement.STAGE_REMOVE
    )
    assert finding.verdict == patch_retirement.VERDICT_SAFE_TO_REMOVE


@pytest.mark.parametrize(
    "evidence_kwargs",
    [
        {"retained_markers": frozenset({"run-example-v1"})},
        {"continue_as_new_chains": frozenset({"run-example-v1"})},
        {"pending_old_inputs": frozenset({"run-example-v1"})},
        {"pre_patch_workers_present": True},
        {"running_may_predate_patch": True},
    ],
)
@pytest.mark.parametrize("stage", ["deprecate", "remove"])
def test_consumers_block_both_stages(evidence_kwargs, stage) -> None:
    finding = patch_retirement.audit_patch(
        _record(), _healthy_evidence(**evidence_kwargs), stage=stage
    )
    assert finding.verdict == patch_retirement.VERDICT_REQUIRES_COMPATIBILITY


@pytest.mark.parametrize(
    "evidence_kwargs",
    [
        {"visibility_failures": ("visibility query timed out",)},
        {"history_failures": ("history fetch unavailable",)},
        {"stale_visibility": True},
    ],
)
def test_failed_or_stale_evidence_is_unknown_not_zero_consumers(
    evidence_kwargs,
) -> None:
    """A failed query must not report zero consumers."""
    for stage in (patch_retirement.STAGE_DEPRECATE, patch_retirement.STAGE_REMOVE):
        finding = patch_retirement.audit_patch(
            _record(), _healthy_evidence(**evidence_kwargs), stage=stage
        )
        assert finding.verdict == patch_retirement.VERDICT_UNKNOWN


def test_unsupported_stage_rejected() -> None:
    with pytest.raises(ValueError):
        patch_retirement.audit_patch(_record(), _healthy_evidence(), stage="delete")


# --- Admission gating (REQ-3) -------------------------------------------------


def test_gate_routes_new_work_while_compat_required() -> None:
    record = _record()
    evidence = _healthy_evidence(retained_markers=frozenset({"run-example-v1"}))
    disposition, reason = patch_retirement.retirement_gate(record, evidence)
    assert disposition == "route_new_only"
    assert reason


def test_gate_blocks_on_unknown_evidence() -> None:
    record = _record()
    evidence = _healthy_evidence(visibility_failures=("boom",))
    disposition, _ = patch_retirement.retirement_gate(record, evidence)
    assert disposition == "block_unknown"


def test_gate_open_when_no_consumers() -> None:
    disposition, _ = patch_retirement.retirement_gate(_record(), _healthy_evidence())
    assert disposition == "open"


# --- Bounded, read-only report (AC-1) ----------------------------------------


def test_report_bound_discloses_truncation() -> None:
    records = [_record(f"run-example-{index}-v1") for index in range(5)]
    report = patch_retirement.audit_all(
        records, _healthy_evidence(), max_entries=2
    )
    assert len(report.findings) == 2
    assert report.truncated == 3


def test_audit_does_not_mutate_inputs() -> None:
    records = [_record()]
    evidence = _healthy_evidence()
    records_snapshot = list(records)
    patch_retirement.audit_all(records, evidence)
    assert list(records) == records_snapshot
    assert evidence == _healthy_evidence()


def test_cli_without_evidence_reports_unknown(tmp_path: Path, capsys) -> None:
    argv = [
        "--repo-root",
        str(REPO_ROOT),
        "--format",
        "json",
        "--max-entries",
        "3",
    ]
    assert patch_retirement.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["findings"]
    assert all(f["verdict"] == "unknown" for f in payload["findings"])
    assert payload["unknownEvidence"]


def test_cli_with_healthy_evidence(tmp_path: Path, capsys) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "deploymentVersions": ["2026.09.12"],
                "admissionCutoff": "2026-09-12T00:00:00Z",
                "resetReplaySupported": False,
                "visibilityFailures": [],
                "historyFailures": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        patch_retirement.main(
            [
                "--repo-root",
                str(REPO_ROOT),
                "--format",
                "markdown",
                "--evidence-json",
                str(evidence_path),
                "--max-entries",
                "5",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "safe_to_deprecate: 5" in out


def test_evidence_from_mapping_defaults_conservative() -> None:
    evidence = patch_retirement.evidence_from_mapping({})
    assert evidence.reset_replay_supported is True
    assert evidence.retained_markers == frozenset()
    # Silence about query health reads as unknown, never as no consumers.
    assert evidence.visibility_failures
    assert evidence.history_failures


def test_evidence_from_mapping_explicit_empty_means_healthy() -> None:
    evidence = patch_retirement.evidence_from_mapping(
        {"visibilityFailures": [], "historyFailures": []}
    )
    assert evidence.visibility_failures == ()
    assert evidence.history_failures == ()


# --- Retirement replay (REQ-5, AC-2, AC-4) ------------------------------------


@workflow.defn(name="ConditionalRegistryRetirementFixture")
class _LegacyConditionalRegistryFixture:
    @workflow.run
    async def run(self) -> str:
        workflow.patched(CONDITIONAL_REGISTRY_PATCH)
        return "legacy"


@workflow.defn(name="ConditionalRegistryRetirementFixture")
class _CurrentConditionalRegistryFixture:
    @workflow.run
    async def run(self) -> str:
        workflow.deprecate_patch(CONDITIONAL_REGISTRY_PATCH)
        return "current"


@pytest.mark.asyncio
@pytest.mark.temporal_boundary
async def test_retired_marker_old_and_new_histories_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-change (marked) and post-change (unmarked) histories both replay
    against the deprecated call site: the actual removal bridge, not deleted
    test cases."""
    # Restore genuine SDK marker semantics: the conftest no-op would hide the
    # recorded marker commands this test must exercise.
    monkeypatch.setattr(
        temporal_workflow_module, "deprecate_patch", _REAL_DEPRECATE_PATCH
    )
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for kind, implementation in (
            ("legacy", _LegacyConditionalRegistryFixture),
            ("current", _CurrentConditionalRegistryFixture),
        ):
            queue = f"test-conditional-registry-retirement-{kind}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    id=f"{queue}-id",
                    task_queue=queue,
                )
                await handle.result()
                histories.append(await handle.fetch_history())
    assert len(histories) == 2
    replayer = Replayer(
        workflows=[_CurrentConditionalRegistryFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
