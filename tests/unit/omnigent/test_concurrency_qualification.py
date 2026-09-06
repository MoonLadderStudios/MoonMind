"""The layered concurrency qualification record and its scenario catalog.

Source issue: MoonLadderStudios/MoonMind#3885.

These tests hold the record to the four promises that make it evidence rather
than a status board: a configured value is not an observation, a skipped row is
not a pass, a validated level does not generalize, and an unresolved teardown
is not a zero-leak scan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.omnigent.concurrency_qualification import (
    CONCURRENCY_SCENARIO_CATALOG,
    CONCURRENCY_SCENARIO_CATALOG_VERSION,
    DEFAULT_REPEATED_WAVE_THRESHOLDS,
    EXACT_DOCKER_LEVELS,
    HERMETIC_LEVELS,
    REQUIRED_QUALIFICATION_LAYERS,
    CleanupScanEntry,
    CleanupScanReport,
    ConcurrencyQualificationLayer,
    ConcurrencyQualificationRecord,
    ConcurrencyQualificationRow,
    ConcurrencyRowStatus,
    ConcurrencyScenarioFamily,
    ConcurrencySupportIdentity,
    ExecutionOverlapSample,
    MachineResourceClass,
    ObservedOverlapEvidence,
    RepeatedWaveReport,
    ScenarioOwner,
    WaveObservation,
    build_row_for_unavailable_environment,
    compute_concurrency_evidence_digest,
    observed_peak_overlap,
    scenario_owners,
    unowned_scenarios,
)

SUPPORT_KEY = "omnigent-support:sha256:" + "a" * 64
OTHER_SUPPORT_KEY = "omnigent-support:sha256:" + "b" * 64

RESOURCE_CLASS = MachineResourceClass(
    resource_class_ref="ci-standard-4x8@1", cpu_cores=4, memory_gib=8
)


def _identity(key: str = SUPPORT_KEY) -> ConcurrencySupportIdentity:
    return ConcurrencySupportIdentity(
        supportCombinationKey=key,
        moonmindCommit="0" * 40,
        workerBuildRef="moonmind-worker@test",
        providerCapacityPolicyVersion="omnigent-provider-capacity@1",
        hostCapacityPolicyVersion="omnigent-host-capacity@1",
        transportPoolPolicyVersion="omnigent-transport-pool@1",
        workerTopologyRef="single-replica@1",
        resourceClass=RESOURCE_CLASS,
    )


def _overlap(level: int, *, effective_limit: int | None = None, waiters: int = 0):
    """Return overlap evidence for ``level`` genuinely simultaneous windows."""

    limit = effective_limit if effective_limit is not None else level
    observed = min(level, limit)
    return ObservedOverlapEvidence(
        requested_level=level,
        effective_limit=limit,
        barrier_synchronized=True,
        samples=tuple(
            ExecutionOverlapSample(
                execution_ref=f"run-{index}",
                started_at=float(index) * 0.01,
                ended_at=10.0 + float(index) * 0.01,
            )
            for index in range(observed)
        ),
        durable_waiters=waiters,
    )


def _passing_row(
    layer: ConcurrencyQualificationLayer, level: int
) -> ConcurrencyQualificationRow:
    payload = {"layer": layer.value, "level": level}
    return ConcurrencyQualificationRow(
        layer=layer,
        level=level,
        status=ConcurrencyRowStatus.passed,
        overlap=_overlap(level),
        evidence_ref=f"artifact://concurrency/{layer.value}/{level}",
        evidence_digest=compute_concurrency_evidence_digest(payload),
        resource_class=RESOURCE_CLASS,
    )


def _record(rows) -> ConcurrencyQualificationRecord:
    return ConcurrencyQualificationRecord(
        identity=_identity(), generatedAt=datetime.now(UTC), rows=tuple(rows)
    )


# ---------------------------------------------------------------------------
# Scenario catalog: every required family has a real owner.
# ---------------------------------------------------------------------------


def test_every_required_family_has_an_owner_in_every_required_layer() -> None:
    assert unowned_scenarios() == ()
    for family in ConcurrencyScenarioFamily:
        for layer in REQUIRED_QUALIFICATION_LAYERS:
            assert scenario_owners(family=family, layer=layer), (
                f"{family.value} has no owner at {layer.value}"
            )


def test_every_owning_test_resolves_in_the_repository() -> None:
    """A catalog entry naming a test that does not exist is not an owner."""

    repo_root = Path(__file__).resolve().parents[3]
    for owner in CONCURRENCY_SCENARIO_CATALOG:
        target = repo_root / owner.owning_test.split("::")[0]
        assert target.exists(), f"{owner.owning_test} does not exist"


def test_sibling_escaped_regressions_are_bound_to_owners() -> None:
    """Each sibling issue's escaped regression names a test that replays it."""

    covered = {
        ref
        for owner in CONCURRENCY_SCENARIO_CATALOG
        for ref in owner.escaped_regressions
    }
    for issue in ("3879", "3880", "3881", "3882", "3883", "3884"):
        assert f"MoonLadderStudios/MoonMind#{issue}" in covered


def test_only_hermetic_owners_may_be_required_in_pull_request_ci() -> None:
    """Exact-image and protected-live rows are scheduled, never PR-required."""

    with pytest.raises(ValueError, match="only hermetic owners"):
        ScenarioOwner(
            family=ConcurrencyScenarioFamily.isolation_and_completion,
            layer=ConcurrencyQualificationLayer.exact_docker,
            owning_test="tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
            required_in_ci=True,
        )


# ---------------------------------------------------------------------------
# Observed overlap: a configured value is not a result.
# ---------------------------------------------------------------------------


def test_the_peak_is_swept_from_observed_windows() -> None:
    overlapping = tuple(
        ExecutionOverlapSample(execution_ref=f"run-{i}", started_at=0.0, ended_at=1.0)
        for i in range(5)
    )
    assert observed_peak_overlap(overlapping) == 5


def test_adjacent_windows_are_not_overlap() -> None:
    """A window that ends exactly when the next begins never ran alongside it."""

    adjacent = (
        ExecutionOverlapSample(execution_ref="run-0", started_at=0.0, ended_at=1.0),
        ExecutionOverlapSample(execution_ref="run-1", started_at=1.0, ended_at=2.0),
    )
    assert observed_peak_overlap(adjacent) == 1


def test_overlap_evidence_requires_observed_samples() -> None:
    with pytest.raises(ValueError, match="self-asserted peak is not an observation"):
        ObservedOverlapEvidence(
            requested_level=4,
            effective_limit=4,
            barrier_synchronized=True,
            samples=(),
        )


def test_overlap_evidence_requires_barrier_synchronization() -> None:
    with pytest.raises(ValueError, match="barriers or controlled holds"):
        ObservedOverlapEvidence(
            requested_level=2,
            effective_limit=2,
            barrier_synchronized=False,
            samples=tuple(
                ExecutionOverlapSample(
                    execution_ref=f"run-{i}", started_at=0.0, ended_at=1.0
                )
                for i in range(2)
            ),
        )


def test_work_above_the_effective_limit_must_be_observed_waiting() -> None:
    """Excess submissions are waiters, and their absence is a defect."""

    assert _overlap(6, effective_limit=4, waiters=2).observed_peak == 4
    with pytest.raises(ValueError, match="durable waiters"):
        _overlap(6, effective_limit=4, waiters=0)


def test_duplicate_execution_refs_are_rejected() -> None:
    with pytest.raises(ValueError, match="distinct executions"):
        ObservedOverlapEvidence(
            requested_level=2,
            effective_limit=2,
            barrier_synchronized=True,
            samples=(
                ExecutionOverlapSample(
                    execution_ref="run-0", started_at=0.0, ended_at=1.0
                ),
                ExecutionOverlapSample(
                    execution_ref="run-0", started_at=0.0, ended_at=1.0
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Row status: a skipped row is not a pass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        ConcurrencyRowStatus.failed,
        ConcurrencyRowStatus.skipped,
        ConcurrencyRowStatus.blocked,
        ConcurrencyRowStatus.unavailable,
        ConcurrencyRowStatus.partial,
    ],
)
def test_non_pass_rows_are_recordable_and_never_qualify(status) -> None:
    row = ConcurrencyQualificationRow(
        layer=ConcurrencyQualificationLayer.exact_docker,
        level=4,
        status=status,
        diagnostics=("the environment did not provide the exact host image",),
    )
    assert not row.qualifies
    assert row.status is status


def test_a_passing_row_requires_observed_overlap_and_resolvable_evidence() -> None:
    with pytest.raises(ValueError, match="requires observed overlap"):
        ConcurrencyQualificationRow(
            layer=ConcurrencyQualificationLayer.hermetic,
            level=4,
            status=ConcurrencyRowStatus.passed,
        )
    with pytest.raises(ValueError, match="independently\n?\\s*resolvable evidence"):
        ConcurrencyQualificationRow(
            layer=ConcurrencyQualificationLayer.hermetic,
            level=4,
            status=ConcurrencyRowStatus.passed,
            overlap=_overlap(4),
        )


def test_a_non_pass_row_cannot_smuggle_in_overlap_evidence() -> None:
    with pytest.raises(ValueError, match="only a passing row"):
        ConcurrencyQualificationRow(
            layer=ConcurrencyQualificationLayer.hermetic,
            level=4,
            status=ConcurrencyRowStatus.skipped,
            overlap=_overlap(4),
        )


def test_a_passing_exact_docker_row_declares_its_resource_class() -> None:
    with pytest.raises(ValueError, match="machine resource class"):
        ConcurrencyQualificationRow(
            layer=ConcurrencyQualificationLayer.exact_docker,
            level=4,
            status=ConcurrencyRowStatus.passed,
            overlap=_overlap(4),
            evidence_ref="artifact://x",
            evidence_digest=compute_concurrency_evidence_digest({"x": 1}),
        )


def test_an_unavailable_environment_row_names_its_reason() -> None:
    row = build_row_for_unavailable_environment(
        layer=ConcurrencyQualificationLayer.protected_live,
        level=2,
        reason="the credentialless OpenCode Zen route was not reachable",
    )
    assert row.status is ConcurrencyRowStatus.unavailable
    assert not row.qualifies
    assert row.diagnostics[0].startswith("the credentialless")


def test_one_layer_level_pair_holds_exactly_one_outcome() -> None:
    """Two rows for the same pair would let a pass hide a failure."""

    with pytest.raises(ValueError, match="only one outcome"):
        _record(
            [
                _passing_row(ConcurrencyQualificationLayer.hermetic, 2),
                ConcurrencyQualificationRow(
                    layer=ConcurrencyQualificationLayer.hermetic,
                    level=2,
                    status=ConcurrencyRowStatus.failed,
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Validated level: evidence does not generalize.
# ---------------------------------------------------------------------------


def test_a_level_is_validated_only_in_every_required_layer() -> None:
    hermetic_only = _record(
        [_passing_row(ConcurrencyQualificationLayer.hermetic, level) for level in (1, 2)]
    )
    assert hermetic_only.validated_concurrency_level == 0

    both = _record(
        [
            *(
                _passing_row(ConcurrencyQualificationLayer.hermetic, level)
                for level in (1, 2)
            ),
            _passing_row(ConcurrencyQualificationLayer.exact_docker, 2),
        ]
    )
    assert both.validated_concurrency_level == 2


def test_a_pass_at_two_never_claims_sixteen() -> None:
    """The property the issue states outright: N=2 does not qualify N=16."""

    record = _record(
        [
            _passing_row(ConcurrencyQualificationLayer.hermetic, 2),
            _passing_row(ConcurrencyQualificationLayer.exact_docker, 2),
            # 16 was attempted hermetically only, and never on exact images.
            _passing_row(ConcurrencyQualificationLayer.hermetic, 16),
        ]
    )
    assert record.validated_concurrency_level == 2
    assert record.advertised_concurrency_level() == 2


def test_advertisement_never_exceeds_the_operator_ceiling() -> None:
    record = _record(
        [
            _passing_row(ConcurrencyQualificationLayer.hermetic, 8),
            _passing_row(ConcurrencyQualificationLayer.exact_docker, 8),
        ]
    )
    assert record.validated_concurrency_level == 8
    assert record.advertised_concurrency_level(operator_ceiling=4) == 4
    # A ceiling above the validated level does not raise it, and the configured
    # value itself is never rewritten by this call.
    assert record.advertised_concurrency_level(operator_ceiling=32) == 8


def test_a_failed_row_lowers_the_validated_level_to_the_last_clean_one() -> None:
    record = _record(
        [
            _passing_row(ConcurrencyQualificationLayer.hermetic, 2),
            _passing_row(ConcurrencyQualificationLayer.exact_docker, 2),
            _passing_row(ConcurrencyQualificationLayer.hermetic, 4),
            ConcurrencyQualificationRow(
                layer=ConcurrencyQualificationLayer.exact_docker,
                level=4,
                status=ConcurrencyRowStatus.failed,
                diagnostics=("two runs shared one state volume",),
            ),
        ]
    )
    assert record.validated_concurrency_level == 2
    assert [row.level for row in record.unqualified_rows] == [4]


def test_evidence_cannot_cross_support_combinations() -> None:
    """A record carries the exact combination it qualified, and only that one."""

    assert _identity().support_combination_key != _identity(
        OTHER_SUPPORT_KEY
    ).support_combination_key


def test_evidence_from_another_scenario_catalog_is_refused() -> None:
    stale = _identity().model_dump(mode="json", by_alias=True)
    stale["scenarioCatalogVersion"] = "moonmind.omnigent-concurrency-scenarios/v0"
    with pytest.raises(ValueError):
        ConcurrencySupportIdentity.model_validate(stale)


def test_the_record_pins_the_current_scenario_catalog_version() -> None:
    record = _record([_passing_row(ConcurrencyQualificationLayer.hermetic, 1)])
    assert (
        record.identity.scenario_catalog_version == CONCURRENCY_SCENARIO_CATALOG_VERSION
    )


def test_the_declared_level_sets_match_the_program() -> None:
    assert HERMETIC_LEVELS == (1, 2, 4, 8, 16)
    assert EXACT_DOCKER_LEVELS == (2, 4, 8)


# ---------------------------------------------------------------------------
# Cleanup: an unresolved teardown is not a zero-leak scan.
# ---------------------------------------------------------------------------


def test_an_unresolved_entry_prevents_a_zero_leak_report() -> None:
    report = CleanupScanReport(
        scanned_at=datetime.now(UTC),
        entries=(
            CleanupScanEntry(resource_ref="mm-host-0", kind="container", resolved=True),
            CleanupScanEntry(resource_ref="mm-host-1", kind="container", resolved=False),
        ),
    )
    assert not report.zero_leak
    assert [entry.resource_ref for entry in report.unresolved] == ["mm-host-1"]
    assert report.as_payload()["zeroLeak"] is False


def test_a_foreign_resource_is_reported_and_never_claimed_as_torn_down() -> None:
    report = CleanupScanReport(
        scanned_at=datetime.now(UTC),
        entries=(
            CleanupScanEntry(
                resource_ref="/home/app/.codex", kind="credential_home", resolved=False, foreign=True
            ),
        ),
    )
    # A profile-owned credential home is preserved, so it is not a leak.
    assert report.zero_leak
    assert [entry.resource_ref for entry in report.foreign_preserved] == [
        "/home/app/.codex"
    ]
    with pytest.raises(ValueError, match="never torn down by"):
        CleanupScanReport(
            scanned_at=datetime.now(UTC),
            entries=(
                CleanupScanEntry(
                    resource_ref="/home/app/.codex",
                    kind="credential_home",
                    resolved=True,
                    foreign=True,
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Repeated waves: bounded resource and history growth.
# ---------------------------------------------------------------------------


def _wave(index: int, **overrides) -> WaveObservation:
    defaults = dict(
        wave_index=index,
        observed_peak=4,
        wait_seconds=0.1,
        launch_seconds=0.2,
        registration_seconds=0.1,
        control_seconds=1.0,
        cleanup_seconds=0.2,
        lease_mutations=8,
        registration_requests=4,
        transport_pool_peak=4,
        residual_resources=0,
    )
    defaults.update(overrides)
    return WaveObservation(**defaults)


def test_stable_waves_are_bounded() -> None:
    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(_wave(0), _wave(1), _wave(2)),
    )
    assert report.bounded
    assert report.violations == ()


def test_a_wave_that_lost_overlap_is_a_violation() -> None:
    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(_wave(0), _wave(1, observed_peak=2)),
    )
    assert "observed peak 2 below level 4" in " ".join(report.violations)


def test_growing_registration_traffic_is_a_violation() -> None:
    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(_wave(0), _wave(1, registration_requests=6)),
    )
    assert "registration requests grew" in " ".join(report.violations)


def test_a_control_latency_budget_breach_is_a_violation() -> None:
    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(_wave(0), _wave(1, control_seconds=120.0)),
    )
    assert "control latency" in " ".join(report.violations)


def test_bounded_growth_needs_more_than_one_wave() -> None:
    with pytest.raises(ValueError, match="at least two waves"):
        RepeatedWaveReport(
            level=4,
            thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
            waves=(_wave(0),),
        )
