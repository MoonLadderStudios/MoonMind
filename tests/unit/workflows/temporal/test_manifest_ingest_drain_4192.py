"""Drain-ownership contract for the retired ManifestIngest workflow type.

MoonLadderStudios/MoonMind#4192 (MR5 manifest removal): deploying the
release whose workflow fleet does not register MoonMind.ManifestIngest
requires an empty-history cutover verified through the versioned drain
gate. These tests execute the gate predicate without a Temporal server,
database, or deployment probe.
"""

from __future__ import annotations

import pytest

from moonmind.gates.manifest_ingest_drain import (
    MANIFEST_INGEST_DRAIN_CONTRACT,
    ManifestIngestDrainObservations,
    ManifestIngestDrainUsage,
    collect_manifest_ingest_drain_observations,
    evaluate_manifest_ingest_drain,
    evaluate_manifest_ingest_drain_observations,
    render_manifest_ingest_drain_report,
    retention_reason,
)


def test_drained_deployment_unblocks_manifest_removal():
    decision = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage())
    assert decision.outstanding == 0
    assert decision.may_deploy_removal is True
    assert decision.required_action == "safe_to_remove"
    assert decision.blocking_dimensions == ()
    assert decision.contract == MANIFEST_INGEST_DRAIN_CONTRACT


@pytest.mark.parametrize(
    "usage",
    [
        ManifestIngestDrainUsage(open_manifest_ingest_histories=1),
        ManifestIngestDrainUsage(pending_manifest_tasks=2),
        ManifestIngestDrainUsage(existing_manifest_schedules=1),
        ManifestIngestDrainUsage(
            open_manifest_ingest_histories=3,
            pending_manifest_tasks=2,
            existing_manifest_schedules=1,
        ),
    ],
)
def test_any_outstanding_dimension_retains_old_release(usage):
    decision = evaluate_manifest_ingest_drain(usage)
    assert decision.may_deploy_removal is False
    assert decision.required_action == "retain_and_drain"
    assert decision.outstanding > 0
    assert decision.blocking_dimensions != ()


def test_unobservable_dimension_is_fail_closed():
    observations = collect_manifest_ingest_drain_observations(
        open_manifest_ingest_histories=None,
        pending_manifest_tasks=0,
        existing_manifest_schedules=0,
    )
    decision = evaluate_manifest_ingest_drain_observations(observations)
    assert decision.may_deploy_removal is False
    assert decision.required_action == "retain_and_drain"
    assert "open_manifest_ingest_histories" in decision.blocking_dimensions


def test_negative_counts_raise():
    with pytest.raises(ValueError):
        ManifestIngestDrainUsage(open_manifest_ingest_histories=-1)
    with pytest.raises(ValueError):
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=-2,
            existing_manifest_schedules=0,
        )


def test_report_names_probes_and_checklist():
    decision = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage())
    report = render_manifest_ingest_drain_report(decision)
    assert MANIFEST_INGEST_DRAIN_CONTRACT in report
    assert "open_manifest_ingest_histories" in report
    assert "Removal checklist" in report
    assert retention_reason(decision).startswith(MANIFEST_INGEST_DRAIN_CONTRACT)
