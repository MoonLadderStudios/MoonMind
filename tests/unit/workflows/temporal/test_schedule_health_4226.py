"""MoonLadderStudios/MoonMind#4226: SkippedOverlap schedule health."""

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
