"""The layered concurrency qualification record and its scenario catalog.

Source issue: MoonLadderStudios/MoonMind#3885.

These tests hold the record to the four promises that make it evidence rather
than a status board: a configured value is not an observation, a skipped row is
not a pass, a validated level does not generalize, and an unresolved teardown
is not a zero-leak scan.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.omnigent.concurrency_qualification import (
    CONCURRENCY_EVIDENCE_DIR_ENV,
    CONCURRENCY_LEVEL_ENV,
    CONCURRENCY_SCENARIO_CATALOG,
    CONCURRENCY_SCENARIO_CATALOG_VERSION,
    DEFAULT_REPEATED_WAVE_THRESHOLDS,
    EXACT_DOCKER_LEVELS,
    EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS,
    HERMETIC_LEVELS,
    REPEATED_WAVE_THRESHOLDS,
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
    load_observed_overlap,
    nominal_memory_band_mib,
    observed_overlap_evidence_path,
    observed_peak_overlap,
    publish_observed_overlap,
    repeated_wave_thresholds,
    requested_concurrency_level,
    scenario_owners,
    unowned_scenarios,
)
from tools import run_omnigent_concurrency_qualification as runner

SUPPORT_KEY = "omnigent-support:sha256:" + "a" * 64
OTHER_SUPPORT_KEY = "omnigent-support:sha256:" + "b" * 64

#: What a real 4-core/8-GiB machine measures: MemTotal 8,138,000 kB, which is
#: physical RAM minus the kernel's reservation. The class it is filed under
#: names 8 GiB, and no machine of that size ever measures 8192 MiB.
EIGHT_GIB_MEMTOTAL_KIB = 8_138_000
EIGHT_GIB_MEASURED_MIB = EIGHT_GIB_MEMTOTAL_KIB * 1024 // 1024**2

RESOURCE_CLASS = MachineResourceClass(
    resource_class_ref="ci-standard-4x8@1",
    cpu_cores=4,
    memory_mib=EIGHT_GIB_MEASURED_MIB,
)


def _measure_machine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cores: int,
    mem_total_kib: int,
) -> None:
    """Make this process measure the machine a test is about.

    The runner reads the affinity mask, the cgroup interface files and
    ``MemTotal``. Pointing the cgroup paths at a path that does not exist makes
    this machine unconfined through the production readers themselves, so the
    assertion is about the machine under test rather than about whatever host
    happens to run this suite.
    """

    real_sysconf = os.sysconf
    page_size = 4096

    def _sysconf(name):  # type: ignore[no-untyped-def]
        if name == "SC_PAGE_SIZE":
            return page_size
        if name == "SC_PHYS_PAGES":
            return mem_total_kib * 1024 // page_size
        return real_sysconf(name)

    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(cores)))
    monkeypatch.setattr(os, "sysconf", _sysconf)
    absent = Path("/nonexistent/moonmind-cgroup-absent")
    for attribute in (
        "CGROUP_V2_CPU_MAX",
        "CGROUP_V1_CPU_QUOTA",
        "CGROUP_V1_CPU_PERIOD",
        "CGROUP_V2_MEMORY_MAX",
        "CGROUP_V1_MEMORY_MAX",
    ):
        monkeypatch.setattr(runner, attribute, absent)


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
    assert "control control latency" in " ".join(report.violations)


@pytest.mark.parametrize(
    "phase", ["wait", "launch", "registration", "cleanup"]
)
def test_one_stalled_control_operation_breaches_the_budget(phase: str) -> None:
    """A stall in any single control phase fails the wave on its own.

    A saturated deployment does not slow every phase evenly: cancellation or
    teardown stalls behind the event stream while the rest stays fast. A budget
    that only bound the wave total would report that wave as healthy.
    """

    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(_wave(0), _wave(1, **{f"{phase}_seconds": 120.0})),
    )

    assert not report.bounded
    assert f"{phase} control latency 120.0s" in " ".join(report.violations)


def test_bounded_growth_needs_more_than_one_wave() -> None:
    with pytest.raises(ValueError, match="at least two waves"):
        RepeatedWaveReport(
            level=4,
            thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
            waves=(_wave(0),),
        )


# ---------------------------------------------------------------------------
# Published observations: the runner reads exactly what an owning test wrote.
# ---------------------------------------------------------------------------


def test_a_published_observation_round_trips_through_the_runner_path(tmp_path) -> None:
    """The publisher and the loader are one contract, not two conventions."""

    overlap = _overlap(4)

    published = publish_observed_overlap(
        ConcurrencyQualificationLayer.hermetic, overlap, evidence_dir=tmp_path
    )

    assert published == observed_overlap_evidence_path(
        tmp_path, ConcurrencyQualificationLayer.hermetic, 4
    )
    loaded = load_observed_overlap(
        tmp_path, ConcurrencyQualificationLayer.hermetic, 4
    )
    assert loaded is not None
    assert loaded.observed_peak == 4
    assert loaded.barrier_synchronized is True


def test_publishing_is_a_no_op_without_a_requested_evidence_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer running an owning test directly writes nothing."""

    monkeypatch.delenv(CONCURRENCY_EVIDENCE_DIR_ENV, raising=False)

    assert (
        publish_observed_overlap(
            ConcurrencyQualificationLayer.hermetic, _overlap(2)
        )
        is None
    )


def test_an_observation_of_another_level_cannot_be_filed_under_this_one(
    tmp_path,
) -> None:
    """Stale evidence from an earlier level never qualifies a later one."""

    publish_observed_overlap(
        ConcurrencyQualificationLayer.hermetic, _overlap(2), evidence_dir=tmp_path
    )
    stale = observed_overlap_evidence_path(
        tmp_path, ConcurrencyQualificationLayer.hermetic, 2
    )
    stale.rename(
        observed_overlap_evidence_path(
            tmp_path, ConcurrencyQualificationLayer.hermetic, 8
        )
    )

    with pytest.raises(ValueError, match="observed level 2, not 8"):
        load_observed_overlap(tmp_path, ConcurrencyQualificationLayer.hermetic, 8)


def test_the_requested_level_comes_from_the_runner_or_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONCURRENCY_LEVEL_ENV, raising=False)
    assert requested_concurrency_level(default=2) == 2

    monkeypatch.setenv(CONCURRENCY_LEVEL_ENV, "8")
    assert requested_concurrency_level(default=2) == 8

    monkeypatch.setenv(CONCURRENCY_LEVEL_ENV, "eight")
    with pytest.raises(ValueError, match="positive integer"):
        requested_concurrency_level(default=2)


# ---------------------------------------------------------------------------
# The runner's rows: a pass needs an observation, and a missing environment is
# recorded rather than skipped.
# ---------------------------------------------------------------------------


@pytest.fixture
def hermetic_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the hermetic layer's database precondition for row tests.

    These tests are about the row contract, not about provisioning a cluster,
    so they declare the environment the layer requires and replace the owning
    tests themselves.
    """

    monkeypatch.setenv(
        "MOONMIND_TEST_POSTGRES_URL", "postgresql://postgres@127.0.0.1:5432/postgres"
    )


def _runner_args(
    evidence_dir,
    *,
    resource_class: str = "local-deterministic@1",
) -> argparse.Namespace:
    """Return runner arguments for the row tests.

    The default class carries no ``<cores>x<gib>`` dimensions, so these tests
    are about the row contract rather than about the dimensions of whatever
    machine runs the suite. A test that *is* about the machine names a
    dimensioned class and measures the machine it means.
    """

    return argparse.Namespace(
        evidence_dir=str(evidence_dir),
        resource_class=resource_class,
    )


def _identity_argv(**overrides: str) -> list[str]:
    """Return the scheduled exact-image job's own argv, minus the paths."""

    supplied = {
        "--support-combination-key": SUPPORT_KEY,
        "--moonmind-commit": "0" * 40,
        "--worker-build-ref": "moonmind-worker@ci",
        "--worker-topology-ref": "single-replica@1",
        # A dimensionless class, so the argv is self-consistent on whatever
        # machine runs this suite and the assertion is about the flag under
        # test rather than about this runner's core count.
        "--resource-class": "local-deterministic@1",
    }
    supplied.update(overrides)
    return [item for flag, value in supplied.items() for item in (flag, value)]


def test_an_owning_test_that_published_its_observation_records_a_pass(
    tmp_path, monkeypatch: pytest.MonkeyPatch, hermetic_database
) -> None:
    """The producing half of the row contract: published evidence -> passed."""

    def _publish(layer, level, _evidence_dir) -> int:
        publish_observed_overlap(layer, _overlap(level), evidence_dir=tmp_path)
        return 0

    monkeypatch.setattr(runner, "_run_owning_tests", _publish)

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.hermetic, (2, 4)
    )

    assert [row.status for row in rows] == [ConcurrencyRowStatus.passed] * 2
    for row in rows:
        assert row.qualifies
        assert row.overlap is not None
        assert row.overlap.observed_peak == row.level
        assert Path(row.evidence_ref).exists(), "a passing row must resolve"
        assert row.evidence_digest == compute_concurrency_evidence_digest(
            json.loads(Path(row.evidence_ref).read_text(encoding="utf-8"))
        )


def test_an_owning_test_that_published_nothing_stays_partial(
    tmp_path, monkeypatch: pytest.MonkeyPatch, hermetic_database
) -> None:
    """Green tests that observed nothing are honest about it, not a pass."""

    monkeypatch.setattr(
        runner, "_run_owning_tests", lambda _layer, _level, _dir: 0
    )

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.hermetic, (2,)
    )

    assert rows[0].status is ConcurrencyRowStatus.partial
    assert not rows[0].qualifies
    assert "no observed-overlap evidence" in rows[0].diagnostics[0]


def test_a_failing_owning_test_records_a_failed_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch, hermetic_database
) -> None:
    monkeypatch.setattr(
        runner, "_run_owning_tests", lambda _layer, _level, _dir: 1
    )

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.hermetic, (2,)
    )

    assert rows[0].status is ConcurrencyRowStatus.failed
    assert rows[0].overlap is None


@pytest.mark.parametrize(
    "missing",
    [
        "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE",
        "MOONMIND_OMNIGENT_HOST_SERVER_URL",
    ],
)
def test_an_absent_exact_image_environment_records_unavailable_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """A runner missing any exact-image precondition publishes rows, not silence."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "image@sha256:x")
    monkeypatch.setenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "http://omnigent:8000")
    monkeypatch.delenv(missing, raising=False)

    rows = runner.build_rows(
        _runner_args(tmp_path),
        ConcurrencyQualificationLayer.exact_docker,
        EXACT_DOCKER_LEVELS,
    )

    assert [row.level for row in rows] == list(EXACT_DOCKER_LEVELS)
    for row in rows:
        assert row.status is ConcurrencyRowStatus.unavailable
        assert not row.qualifies
        assert row.overlap is None
        assert row.diagnostics, "an unavailable row must name what was missing"


def test_a_hermetic_layer_without_a_database_records_unavailable_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer's real database constraints are an environment, not a test.

    Without a cluster the final-slot race never ran, so recording the fixture
    error as ``failed`` would report a concurrency defect that was never
    observed. The row names the missing dependency instead.
    """

    monkeypatch.delenv("MOONMIND_TEST_POSTGRES_URL", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner.Path, "glob", lambda _self, _pattern: iter(()))

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.hermetic, (2, 4)
    )

    assert [row.status for row in rows] == [ConcurrencyRowStatus.unavailable] * 2
    assert "PostgreSQL" in rows[0].diagnostics[0]


def test_a_configured_database_url_admits_the_hermetic_layer(
    tmp_path, monkeypatch: pytest.MonkeyPatch, hermetic_database
) -> None:
    """A configured cluster is enough; no local binaries are required."""

    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner.Path, "glob", lambda _self, _pattern: iter(()))
    monkeypatch.setattr(runner, "_run_owning_tests", lambda _l, _lv, _d: 0)

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.hermetic, (2,)
    )

    assert rows[0].status is ConcurrencyRowStatus.partial


def test_an_unadmitted_protected_live_layer_records_blocked_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in refusal is a policy outcome, not a missing environment."""

    monkeypatch.delenv(
        "MOONMIND_OMNIGENT_PROTECTED_LIVE_CONCURRENCY", raising=False
    )

    rows = runner.build_rows(
        _runner_args(tmp_path), ConcurrencyQualificationLayer.protected_live, (2,)
    )

    assert rows[0].status is ConcurrencyRowStatus.blocked
    assert not rows[0].qualifies


def test_an_unavailable_required_layer_never_raises_the_validated_level(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermetic passes alone cannot qualify a level the images never ran.

    This is the exact confusion the issue forbids: a green hermetic suite plus
    a skipped exact-image row reads like a passing matrix unless the record
    refuses to count the missing layer.
    """

    monkeypatch.delenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", raising=False)
    monkeypatch.delenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", raising=False)

    exact_rows = runner.build_rows(
        _runner_args(tmp_path),
        ConcurrencyQualificationLayer.exact_docker,
        EXACT_DOCKER_LEVELS,
    )
    record = _record(exact_rows)

    assert record.validated_concurrency_level == 0
    assert record.advertised_concurrency_level(operator_ceiling=8) == 0
    assert len(record.unqualified_rows) == len(EXACT_DOCKER_LEVELS)


# ---------------------------------------------------------------------------
# The invocation gate: a single-layer job is answerable for its own rows.
# ---------------------------------------------------------------------------


def _run_main(tmp_path, layer: str, levels: str, rows) -> int:
    """Invoke the runner CLI with ``build_rows`` replaced by fixed outcomes."""

    import unittest.mock

    with unittest.mock.patch.object(
        runner, "build_rows", lambda _args, _layer, _levels: list(rows)
    ):
        return runner.main(
            [
                "--layer",
                layer,
                "--levels",
                levels,
                "--support-combination-key",
                SUPPORT_KEY,
                "--moonmind-commit",
                "0" * 40,
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--output",
                str(tmp_path / "record.json"),
            ]
        )


def test_a_single_layer_invocation_passes_on_its_own_requested_rows(
    tmp_path,
) -> None:
    """Both CI jobs run one layer, so one layer has to be able to succeed."""

    protected_only = _run_main(
        tmp_path,
        "protected_live",
        "2",
        [_passing_row(ConcurrencyQualificationLayer.protected_live, 2)],
    )
    assert protected_only == 0

    hermetic_only = _run_main(
        tmp_path,
        "hermetic",
        "1,2",
        [
            _passing_row(ConcurrencyQualificationLayer.hermetic, 1),
            _passing_row(ConcurrencyQualificationLayer.hermetic, 2),
        ],
    )
    assert hermetic_only == 0


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
def test_any_non_passing_requested_row_fails_the_invocation(
    tmp_path, status
) -> None:
    code = _run_main(
        tmp_path,
        "exact_docker",
        "2,4",
        [
            _passing_row(ConcurrencyQualificationLayer.exact_docker, 2),
            ConcurrencyQualificationRow(
                layer=ConcurrencyQualificationLayer.exact_docker,
                level=4,
                status=status,
                diagnostics=("the exact images were not present",),
            ),
        ],
    )
    assert code == 1


def test_a_requested_row_that_was_never_produced_fails_the_invocation(
    tmp_path,
) -> None:
    """Silence about a requested level is a failure, not an omission."""

    code = _run_main(
        tmp_path,
        "exact_docker",
        "2,4,8",
        [_passing_row(ConcurrencyQualificationLayer.exact_docker, 2)],
    )
    assert code == 1


def test_the_invocation_gate_is_not_the_cross_layer_validated_level(
    tmp_path,
) -> None:
    """A passing single-layer job still advertises nothing on its own."""

    code = _run_main(
        tmp_path,
        "hermetic",
        "2",
        [_passing_row(ConcurrencyQualificationLayer.hermetic, 2)],
    )
    record = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))

    assert code == 0
    assert (
        ConcurrencyQualificationRecord.model_validate(
            record
        ).validated_concurrency_level
        == 0
    )


def test_the_requested_matrix_defaults_to_each_layers_declared_levels() -> None:
    args = runner._parse_args(
        [
            "--layer",
            "all",
            "--support-combination-key",
            SUPPORT_KEY,
            "--moonmind-commit",
            "0" * 40,
        ]
    )

    matrix = dict(runner.requested_matrix(args))

    assert matrix[ConcurrencyQualificationLayer.hermetic] == HERMETIC_LEVELS
    assert matrix[ConcurrencyQualificationLayer.exact_docker] == EXACT_DOCKER_LEVELS


# ---------------------------------------------------------------------------
# Re-entry: the runner never executes a test that runs the runner.
# ---------------------------------------------------------------------------


def test_no_owning_test_re_enters_the_qualification_runner() -> None:
    """An owning test that called ``build_rows`` would recurse without bound.

    ``_run_owning_tests`` spawns pytest over every owning test for the layer
    with the layer's environment still set. If one of those files asked the
    runner to build the same layer's rows, the environment check would pass
    again and the runner would spawn itself for as long as the machine lasted.
    The record contract is therefore asserted here, in a file the catalog does
    not own.
    """

    repo_root = Path(__file__).resolve().parents[3]
    for owner in CONCURRENCY_SCENARIO_CATALOG:
        target = repo_root / owner.owning_test.split("::")[0]
        source = target.read_text(encoding="utf-8")
        assert "run_omnigent_concurrency_qualification" not in source, (
            f"{owner.owning_test} re-enters the qualification runner; move the "
            "record-contract assertions out of the owning-test set"
        )


# ---------------------------------------------------------------------------
# The support identity is resolved before a layer spends its matrix.
# ---------------------------------------------------------------------------


@pytest.fixture
def never_spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Fail the test if any owning test is spawned.

    The whole point of resolving the identity first is that nothing is spent
    before the record it would be filed under is known to be constructible, so
    "it raised the right error" is only half the assertion.
    """

    spawned: list[tuple] = []

    def _record_spawn(layer, level, evidence_dir) -> int:
        spawned.append((layer, level, evidence_dir))
        return 0

    monkeypatch.setattr(runner, "_run_owning_tests", _record_spawn)
    return spawned


@pytest.mark.parametrize(
    ("flag", "variable"),
    [
        ("--support-combination-key", "OMNIGENT_CONCURRENCY_SUPPORT_KEY"),
        ("--worker-build-ref", "OMNIGENT_WORKER_BUILD_REF"),
        ("--worker-topology-ref", "OMNIGENT_WORKER_TOPOLOGY_REF"),
        ("--resource-class", "OMNIGENT_CONCURRENCY_RESOURCE_CLASS"),
    ],
)
def test_a_blank_identity_argument_fails_before_the_matrix_is_spent(
    tmp_path, never_spawned, flag, variable
) -> None:
    """An unset repository variable must not cost a completed wave.

    The scheduled job passes each of these from ``${{ vars.* }}``. An unset
    GitHub variable expands to the empty string, which argparse accepts *over*
    the flag's default, so the failure has to land before ``build_rows`` runs
    a single level — and it has to name the input the operator can change.
    """

    with pytest.raises(SystemExit) as raised:
        runner.main(
            [
                "--layer",
                "exact_docker",
                "--levels",
                "2,4,8",
                *_identity_argv(**{flag: ""}),
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--output",
                str(tmp_path / "record.json"),
            ]
        )

    message = str(raised.value.code)
    assert flag in message, message
    assert variable in message, message
    assert never_spawned == [], "the matrix ran before the identity was resolved"
    assert not (tmp_path / "record.json").exists()


def test_a_malformed_identity_argument_names_the_flag_not_a_traceback(
    tmp_path, never_spawned
) -> None:
    """A rejected value reports the argument, not a pydantic location."""

    with pytest.raises(SystemExit) as raised:
        runner.main(
            [
                "--layer",
                "exact_docker",
                "--levels",
                "2",
                *_identity_argv(**{"--moonmind-commit": "not-a-commit"}),
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--output",
                str(tmp_path / "record.json"),
            ]
        )

    assert "--moonmind-commit" in str(raised.value.code)
    assert never_spawned == []


def test_a_valid_invocation_writes_its_record_even_when_no_row_passed(
    tmp_path,
) -> None:
    """Evidence survives the gate: the record is written, then the gate fails.

    A record that only exists when the gate passes cannot show an operator why
    a level was not qualified, which is the one question the artifact is
    uploaded to answer.
    """

    code = _run_main(
        tmp_path,
        "exact_docker",
        "2,4",
        [
            ConcurrencyQualificationRow(
                layer=ConcurrencyQualificationLayer.exact_docker,
                level=level,
                status=ConcurrencyRowStatus.failed,
                diagnostics=("owning tests exited 1",),
            )
            for level in (2, 4)
        ],
    )

    assert code == 1
    record = ConcurrencyQualificationRecord.model_validate_json(
        (tmp_path / "record.json").read_text(encoding="utf-8")
    )
    assert [row.status for row in record.rows] == [ConcurrencyRowStatus.failed] * 2
    assert record.validated_concurrency_level == 0


# ---------------------------------------------------------------------------
# The published machine is measured, and the class it is filed under is a claim
# that measurement has to satisfy.
# ---------------------------------------------------------------------------


def test_the_scheduled_exact_image_argv_publishes_a_real_eight_gib_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented invocation must resolve on the machine it documents.

    This is the scheduled job's own argv: the identity flags and
    ``--resource-class ci-standard-4x8@1``, with no way to declare the machine.
    Comparing whole floored GiB against the ref refused it on every honest
    8-GiB host — ``MemTotal`` is physical RAM minus the kernel's reservation,
    so the machine measured 7 GiB against a class naming 8 — and because the
    refusal precedes the row loop the scheduled run produced neither a row nor
    a record.
    """

    _measure_machine(monkeypatch, cores=4, mem_total_kib=EIGHT_GIB_MEMTOTAL_KIB)

    identity = runner.build_identity(
        runner._parse_args(_identity_argv(**{"--resource-class": "ci-standard-4x8@1"}))
    )

    assert identity.resource_class.resource_class_ref == "ci-standard-4x8@1"
    assert identity.resource_class.cpu_cores == 4
    # The published machine is the measurement, not the size the class names.
    assert identity.resource_class.memory_mib == EIGHT_GIB_MEASURED_MIB
    assert identity.resource_class.memory_mib < 8 * 1024


def test_the_class_a_real_runner_publishes_resolves_a_repeated_wave_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only budgeted exact-image class must be one a real machine can publish.

    ``EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS`` is keyed to ``ci-standard-4x8@1``.
    A budget bound to a class no honest machine could name would leave every
    exact-image row without a repeated-wave verdict.
    """

    _measure_machine(monkeypatch, cores=4, mem_total_kib=EIGHT_GIB_MEMTOTAL_KIB)

    identity = runner.build_identity(
        runner._parse_args(_identity_argv(**{"--resource-class": "ci-standard-4x8@1"}))
    )

    assert repeated_wave_thresholds(identity.resource_class.resource_class_ref) is (
        EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS
    )


def test_a_class_ref_cannot_be_published_by_a_machine_it_does_not_name(
    monkeypatch: pytest.MonkeyPatch, never_spawned: list[tuple]
) -> None:
    """A 4x8 row cannot be published from a sixteen-core host.

    No flag can declare the machine, so this is the whole of the abuse surface:
    the ref an operator sets is held to the measurement, and the refusal lands
    before any layer spends its matrix.
    """

    _measure_machine(monkeypatch, cores=16, mem_total_kib=40_681_160)

    with pytest.raises(SystemExit) as raised:
        runner.build_identity(
            runner._parse_args(
                _identity_argv(**{"--resource-class": "ci-standard-4x8@1"})
            )
        )

    message = str(raised.value.code)
    assert "--resource-class" in message
    assert "no layer was run" in message
    assert never_spawned == []


@pytest.mark.parametrize("flag", ("--cpu-cores", "--memory-gib"))
def test_no_flag_can_declare_the_machine(flag: str) -> None:
    """The declaration path is gone, not merely cross-checked.

    While the dimensions were declarable, the invariant the class exists to
    carry was opt-out on exactly the invocation an operator controls: supplying
    ``--cpu-cores 4 --memory-gib 8`` published a ``ci-standard-4x8@1`` row from
    a sixteen-core host without the measurement ever being consulted.
    """

    with pytest.raises(SystemExit):
        runner._parse_args(_identity_argv() + [flag, "4"])


def test_a_resource_class_ref_cannot_contradict_the_machine_it_names() -> None:
    """``ci-standard-4x8@1`` on a 16x64 runner describes substrate that never ran."""

    with pytest.raises(ValueError, match="names a 4-core/8-GiB machine"):
        MachineResourceClass(
            resource_class_ref="ci-standard-4x8@1",
            cpu_cores=16,
            memory_mib=64 * 1024,
        )


def test_the_memory_band_refuses_the_next_smaller_machine() -> None:
    """The near miss, not only the gross mismatch.

    The band exists because no machine measures its nominal size. It is still
    narrow enough that a genuine 7-GiB machine — which measures about 6.8 GiB —
    cannot be filed under a class naming 8, and a machine with *more* memory
    than the class names cannot be filed under it either, because thresholds
    calibrated for the smaller class would pass on headroom it does not
    describe.
    """

    floor_mib, ceiling_mib = nominal_memory_band_mib(8)
    seven_gib_machine = 7 * 1024 * 97 // 100

    assert floor_mib <= EIGHT_GIB_MEASURED_MIB <= ceiling_mib
    for refused in (seven_gib_machine, ceiling_mib + 1):
        with pytest.raises(ValueError, match="names a 4-core/8-GiB machine"):
            MachineResourceClass(
                resource_class_ref="ci-standard-4x8@1",
                cpu_cores=4,
                memory_mib=refused,
            )


def test_a_sixteen_gib_ci_runner_can_publish_its_own_class() -> None:
    """The arithmetic has to hold at more than one nominal size.

    A hosted 4-core/16-GiB runner reports ``MemTotal`` around 15.6 GiB. Under a
    floored whole-GiB comparison it published 15 and could name no 16-GiB class
    at all.
    """

    sixteen_gib_measured = 16_373_608 * 1024 // 1024**2

    resource_class = MachineResourceClass(
        resource_class_ref="ci-hosted-4x16@1",
        cpu_cores=4,
        memory_mib=sixteen_gib_measured,
    )

    assert resource_class.memory_mib == sixteen_gib_measured


def test_a_class_ref_without_dimensions_claims_no_machine_size() -> None:
    """``local-deterministic@1`` names no dimensions, so none are checked."""

    resource_class = MachineResourceClass(
        resource_class_ref="local-deterministic@1",
        cpu_cores=16,
        memory_mib=64 * 1024,
    )

    assert (resource_class.cpu_cores, resource_class.memory_mib) == (16, 64 * 1024)


def test_a_passing_exact_docker_row_carries_the_measured_machine(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row's class reflects the machine that ran it, not a CLI constant."""

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "host@sha256:" + "d" * 64
    )
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_HOST_SERVER_URL", "https://omnigent.invalid"
    )
    monkeypatch.setattr(runner, "_docker_available", lambda: None)
    _measure_machine(monkeypatch, cores=32, mem_total_kib=131_600_000)

    def _publish(layer, level, _evidence_dir) -> int:
        publish_observed_overlap(layer, _overlap(level), evidence_dir=tmp_path)
        return 0

    monkeypatch.setattr(runner, "_run_owning_tests", _publish)

    rows = runner.build_rows(
        _runner_args(tmp_path, resource_class="ci-large-32x128@1"),
        ConcurrencyQualificationLayer.exact_docker,
        (2,),
    )

    assert rows[0].status is ConcurrencyRowStatus.passed
    assert rows[0].resource_class is not None
    assert rows[0].resource_class.cpu_cores == 32
    assert rows[0].resource_class.memory_mib == 131_600_000 * 1024 // 1024**2


def test_an_unmeasurable_machine_spends_nothing_and_says_why(
    monkeypatch: pytest.MonkeyPatch, never_spawned: list[tuple]
) -> None:
    """A machine that cannot be measured cannot publish a class at all.

    There is no declaration to fall back to: the published class is a
    measurement, so an unmeasurable machine fails before a layer runs rather
    than filing rows under a machine nobody observed.
    """

    def _unobservable() -> int:
        raise runner.MachineNotObservable("no affinity mask is exposed")

    monkeypatch.setattr(runner, "observed_cpu_cores", _unobservable)

    with pytest.raises(SystemExit) as raised:
        runner.build_identity(runner._parse_args(_identity_argv()))

    message = str(raised.value.code)
    assert "no affinity mask is exposed" in message
    assert "no layer was run" in message
    assert never_spawned == []


def test_the_measured_machine_is_the_one_this_process_may_use() -> None:
    """The observation is a measurement of this machine, not a constant."""

    assert runner.observed_cpu_cores() >= 1
    assert runner.observed_memory_mib() >= 1


def test_a_cgroup_confined_runner_publishes_its_own_limits(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container-confined runner must not publish the host's dimensions.

    A CFS quota never appears in the affinity mask and ``SC_PHYS_PAGES`` is
    host physical memory, so a self-hosted runner inside a limited container
    measured the machine around it rather than the machine it was given.
    """

    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("400000 100000\n", encoding="utf-8")
    memory_max = tmp_path / "memory.max"
    memory_max.write_text(f"{8 * 1024**3}\n", encoding="utf-8")
    _measure_machine(monkeypatch, cores=64, mem_total_kib=264_000_000)
    # The helper leaves this machine unconfined; this test is about confinement,
    # so the same production readers are pointed at the files above.
    monkeypatch.setattr(runner, "CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(runner, "CGROUP_V2_MEMORY_MAX", memory_max)

    assert runner.cgroup_cpu_quota_cores() == 4
    assert runner.cgroup_memory_limit_bytes() == 8 * 1024**3
    assert runner.observed_cpu_cores() == 4
    assert runner.observed_memory_mib() == 8 * 1024


def test_an_unconfined_machine_reads_no_cgroup_ceiling(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``max`` and an absent v1 hierarchy are not limits."""

    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("max 100000\n", encoding="utf-8")
    memory_max = tmp_path / "memory.max"
    memory_max.write_text("max\n", encoding="utf-8")
    monkeypatch.setattr(runner, "CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(runner, "CGROUP_V2_MEMORY_MAX", memory_max)
    monkeypatch.setattr(runner, "CGROUP_V1_CPU_QUOTA", tmp_path / "absent")
    monkeypatch.setattr(runner, "CGROUP_V1_CPU_PERIOD", tmp_path / "absent")
    monkeypatch.setattr(runner, "CGROUP_V1_MEMORY_MAX", tmp_path / "absent")

    assert runner.cgroup_cpu_quota_cores() is None
    assert runner.cgroup_memory_limit_bytes() is None


def test_a_cgroup_v1_unlimited_sentinel_is_not_a_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cgroup v1 spells "unlimited" as a saturated integer, not a word."""

    memory_max = tmp_path / "memory.limit_in_bytes"
    memory_max.write_text("9223372036854771712\n", encoding="utf-8")
    monkeypatch.setattr(runner, "CGROUP_V2_MEMORY_MAX", tmp_path / "absent")
    monkeypatch.setattr(runner, "CGROUP_V1_MEMORY_MAX", memory_max)

    assert runner.cgroup_memory_limit_bytes() is None


# ---------------------------------------------------------------------------
# Repeated-wave budgets bind to the class the wave actually ran on.
# ---------------------------------------------------------------------------


def test_every_required_layer_has_a_declared_repeated_wave_budget() -> None:
    """A budget exists for the deterministic *and* the exact-image class."""

    assert repeated_wave_thresholds("local-deterministic@1") is (
        DEFAULT_REPEATED_WAVE_THRESHOLDS
    )
    assert repeated_wave_thresholds("ci-standard-4x8@1") is (
        EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS
    )
    for ref, thresholds in REPEATED_WAVE_THRESHOLDS.items():
        assert thresholds.resource_class_ref == ref


def test_an_undeclared_resource_class_borrows_no_budget() -> None:
    """An unbudgeted class has no verdict to offer, so it is refused."""

    with pytest.raises(ValueError, match="no repeated-wave budget is declared"):
        repeated_wave_thresholds("ci-mystery-machine@1")


def test_the_exact_image_budget_widens_only_the_control_latency() -> None:
    """Real containers are slower; per-execution control work is not.

    A budget that scaled the mutation, registration or pool counts with the
    machine would let an exact-image wave hide the per-execution growth the
    repeated-wave program exists to catch.
    """

    deterministic = DEFAULT_REPEATED_WAVE_THRESHOLDS
    exact = EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS

    assert exact.max_control_seconds > deterministic.max_control_seconds
    assert (
        exact.max_lease_mutations_per_execution
        == deterministic.max_lease_mutations_per_execution
    )
    assert (
        exact.max_registration_requests_per_execution
        == deterministic.max_registration_requests_per_execution
    )
    assert exact.max_transport_pool_peak == deterministic.max_transport_pool_peak
