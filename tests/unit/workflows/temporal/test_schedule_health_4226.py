"""MoonLadderStudios/MoonMind#4226: SkippedOverlap schedule health."""

from datetime import UTC, datetime
from typing import Any

import pytest

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    ManagedSessionReapResult,
)
from moonmind.workflows.temporal.schedule_health import (
    SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
    evaluate_reconcile_schedules,
    evaluate_schedule_skipped_overlap,
    extract_skipped_overlap,
)


def test_extract_skipped_overlap_supports_dict_and_object_shapes() -> None:
    assert extract_skipped_overlap({"info": {"skippedOverlap": 49}}) == 49
    assert extract_skipped_overlap({"skipped_overlap": 3}) == 3
    assert extract_skipped_overlap({}) is None

    class Info:
        skipped_overlap = 7

    class Description:
        info = Info()

    assert extract_skipped_overlap(Description()) == 7


def test_growing_skipped_overlap_produces_one_diagnostic() -> None:
    outcome = evaluate_schedule_skipped_overlap(
        schedule_id="mm-schedule:abc",
        description={"info": {"skippedOverlap": 49}},
        previous_skipped=43,
        threshold=SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
    )
    assert outcome["currentSkipped"] == 49
    assert outcome["delta"] == 6
    assert outcome["diagnostic"] is not None
    assert outcome["diagnostic"]["code"] == "SCHEDULE_SKIPPED_OVERLAP"
    assert outcome["coalesced"] is False


def test_below_threshold_produces_no_diagnostic() -> None:
    outcome = evaluate_schedule_skipped_overlap(
        schedule_id="mm-schedule:abc",
        description={"info": {"skippedOverlap": 44}},
        previous_skipped=43,
        threshold=SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
    )
    assert outcome["diagnostic"] is None
    assert outcome["coalesced"] is False


def test_repeats_coalesce_for_same_skipped_counter() -> None:
    outcome = evaluate_schedule_skipped_overlap(
        schedule_id="mm-schedule:abc",
        description={"info": {"skippedOverlap": 49}},
        previous_skipped=43,
        threshold=SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
        last_alerted_skipped=49,
    )
    assert outcome["diagnostic"] is None
    assert outcome["coalesced"] is True


def test_reconcile_tick_reports_diagnostics_and_current_counters() -> None:
    summary = evaluate_reconcile_schedules(
        schedule_descriptions={
            "mm-schedule:abc": {"info": {"skippedOverlap": 49}},
            "mm-schedule:ok": {"info": {"skippedOverlap": 1}},
        },
        previous_counters={"mm-schedule:abc": 43, "mm-schedule:ok": 1},
    )
    assert len(summary["diagnostics"]) == 1
    assert summary["diagnostics"][0]["scheduleId"] == "mm-schedule:abc"
    assert summary["currentCounters"] == {
        "mm-schedule:abc": 49,
        "mm-schedule:ok": 1,
    }
    assert summary["coalesced"] == 0


def _reconcile_session_record(session_id: str) -> dict[str, Any]:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId="wf-run-4226",
        containerId=f"container-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="moonmind:latest",
        controlUrl="http://session-control",
        status="ready",
        workspacePath="/work/agent_jobs/wf-run-4226/repo",
        sessionWorkspacePath="/work/agent_jobs/wf-run-4226/session",
        artifactSpoolPath="/work/agent_jobs/wf-run-4226/artifacts",
        startedAt=datetime.now(tz=UTC),
    ).model_dump(mode="json", by_alias=True)


class _ReconcileController4226:
    async def reconcile(self) -> list[dict[str, Any]]:
        return [_reconcile_session_record("sess-4226")]

    async def reap_orphan_session_containers(self) -> ManagedSessionReapResult:
        return ManagedSessionReapResult()


@pytest.mark.asyncio
async def test_reconcile_activity_surfaces_skipped_overlap_diagnostic() -> None:
    """Activity boundary: injected schedule descriptions yield the diagnostic.

    The reconcile activity is intentionally injection-only for schedule
    descriptions (it never describes Temporal schedules itself); the
    scheduler injects ``scheduleDescriptions`` plus the previously observed
    counters. A growing counter (43 -> 49, the #4226 incident shape) must
    surface exactly one ``SCHEDULE_SKIPPED_OVERLAP`` diagnostic while the
    primary reattachment result is preserved.
    """

    activities = TemporalAgentRuntimeActivities(
        session_controller=_ReconcileController4226()  # type: ignore[arg-type]
    )

    result = await activities.agent_runtime_reconcile_managed_sessions(
        {
            "scheduleDescriptions": {
                "mm-schedule:1d72e336": {"info": {"skippedOverlap": 49}},
            },
            "scheduleSkippedPrevious": {"mm-schedule:1d72e336": 43},
        }
    )

    assert result["managedSessionRecordsReconciled"] == 1
    diagnostics = result["scheduleSkippedOverlapDiagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["code"] == "SCHEDULE_SKIPPED_OVERLAP"
    assert diagnostics[0]["scheduleId"] == "mm-schedule:1d72e336"
    assert result["scheduleSkippedCurrent"] == {"mm-schedule:1d72e336": 49}


@pytest.mark.asyncio
async def test_reconcile_activity_coalesces_repeat_skipped_overlap_alert() -> None:
    """Activity boundary: repeats for the same counter do not re-alert."""

    activities = TemporalAgentRuntimeActivities(
        session_controller=_ReconcileController4226()  # type: ignore[arg-type]
    )

    result = await activities.agent_runtime_reconcile_managed_sessions(
        {
            "scheduleDescriptions": {
                "mm-schedule:1d72e336": {"info": {"skippedOverlap": 49}},
            },
            "scheduleSkippedPrevious": {"mm-schedule:1d72e336": 43},
            "scheduleSkippedLastAlerted": {"mm-schedule:1d72e336": 49},
        }
    )

    assert result["managedSessionRecordsReconciled"] == 1
    assert "scheduleSkippedOverlapDiagnostics" not in result
    assert result["scheduleHealth"]["coalesced"] == 1
    assert result["scheduleSkippedCurrent"] == {"mm-schedule:1d72e336": 49}
