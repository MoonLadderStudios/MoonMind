"""Legacy retirement inventory + guard tests.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835.
"""

from __future__ import annotations

import importlib
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.legacy_retirement import (
    OBSOLETE_CONFIGURATION,
    RETIREMENT_INVENTORY,
    TEMPORARY_ROLLOUT_FLAGS,
    ActiveOwnerKind,
    ComponentFamily,
    LegacyAdmissionRejected,
    LegacyPathRecord,
    ObsoleteConfiguration,
    ObsoleteConfigurationError,
    RemovalStage,
    RetentionWindows,
    RetirementClass,
    RetirementCriterion,
    RetirementGuardError,
    RuntimeGeneration,
    assert_inventory_is_complete,
    assert_new_admission_allowed,
    assert_obsolete_configuration,
    assert_retirement_guard,
    assert_surface_admits_new_work,
    assert_temporary_flags_have_retirement,
    evaluate_new_admission,
    evaluate_removal_eligibility,
    evaluate_retirement,
    get_retirement_record,
)
from moonmind.omnigent.retirement_drain import build_drain_evidence
from moonmind.omnigent.retirement_surfaces import discover_legacy_surfaces
from moonmind.omnigent.session_supervisor_rollback import (
    RollbackExerciseDecision,
    RollbackExerciseRecord,
    RollbackScope,
    evaluate_rollback_exercise,
    legacy_rollback_generation_from_settings,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

_BASE_CRITERIA = frozenset({RetirementCriterion.HISTORICAL_READS_AVAILABLE})


def _record(**overrides: object) -> LegacyPathRecord:
    payload: dict[str, object] = {
        "pathId": "omnigent.legacy.fixture",
        "owner": "omnigent-control-plane",
        "description": "Fixture row.",
        "family": ComponentFamily.BRIDGE_COMPATIBILITY,
        "generation": RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        "retirementClass": RetirementClass.TEMPORAL_REPLAY_ONLY,
        "machineCheckableRef": (
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore"
        ),
        "surfaces": (
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore",
        ),
        "replayDependency": True,
        "applicableCriteria": _BASE_CRITERIA,
        "earliestRemovalStage": RemovalStage.REPLAY_WRAPPERS,
        "removalGuardTest": "tests/unit/omnigent/test_legacy_retirement.py::fixture",
    }
    payload.update(overrides)
    return LegacyPathRecord(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------- inventory shape


def test_inventory_is_nonempty_and_nothing_removed_yet() -> None:
    assert RETIREMENT_INVENTORY
    # #3833 has not promoted the qualified generic rows and the replay/rollback
    # evidence in this cohort has not proven replacement coverage, so no legacy
    # implementation may be classified removed.
    assert all(not path.removed for path in RETIREMENT_INVENTORY)


def test_every_component_family_the_issue_enumerates_has_a_row() -> None:
    covered = {path.family for path in RETIREMENT_INVENTORY}
    missing = sorted(
        family.value for family in ComponentFamily if family not in covered
    )
    assert not missing, f"component families with no retirement row: {missing}"


def test_every_row_records_the_nine_required_fields() -> None:
    for path in RETIREMENT_INVENTORY:
        assert path.owner.strip(), path.path_id
        assert isinstance(path.retirement_class, RetirementClass), path.path_id
        # A row either names the source that can still create new legacy work or
        # is explicitly past new admission; the model rejects any other pairing.
        assert bool(path.new_admission_source.strip()) is path.admits_new_work
        assert isinstance(path.active_resource_dependencies, frozenset), path.path_id
        assert isinstance(path.replay_dependency, bool), path.path_id
        assert isinstance(path.historical_read_dependency, bool), path.path_id
        assert isinstance(path.rollback_dependency, bool), path.path_id
        assert path.applicable_criteria, path.path_id
        assert isinstance(path.earliest_removal_stage, RemovalStage), path.path_id
        assert "::" in path.removal_guard_test, path.path_id


def test_removal_guard_tests_name_files_that_exist() -> None:
    from moonmind.omnigent.retirement_surfaces import REPO_ROOT

    for path in RETIREMENT_INVENTORY:
        relative = path.removal_guard_test.split("::", 1)[0]
        assert (REPO_ROOT / relative).is_file(), (
            f"{path.path_id} names removal guard test file {relative} which does "
            "not exist"
        )


def test_new_admission_sources_resolve() -> None:
    for path in RETIREMENT_INVENTORY:
        source = path.new_admission_source
        if not source or source.startswith("docker-compose"):
            continue
        module_name, _, symbol = source.rpartition(":")
        module = importlib.import_module(module_name)
        assert hasattr(module, symbol), (
            f"{path.path_id} names new-admission source {source} which no longer "
            "resolves"
        )


def test_record_rejects_admission_source_on_a_closed_class() -> None:
    with pytest.raises(ValueError, match="does not admit new work"):
        _record(newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution")


def test_record_requires_admission_source_while_it_still_admits() -> None:
    with pytest.raises(ValueError, match="must be named"):
        _record(
            retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
            replayDependency=False,
        )


def test_record_rejects_dependencies_once_eligible_for_removal() -> None:
    with pytest.raises(ValueError, match="cannot carry an active"):
        _record(retirementClass=RetirementClass.ELIGIBLE_FOR_REMOVAL)


def test_record_requires_rollback_allowlist_for_rollback_only() -> None:
    with pytest.raises(ValueError, match="exact permitted rollback generation"):
        _record(
            retirementClass=RetirementClass.ROLLBACK_ONLY,
            newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
            replayDependency=False,
        )


def test_record_rejects_a_surface_ref_without_a_scheme() -> None:
    with pytest.raises(ValueError):
        _record(
            machineCheckableRef="moonmind.omnigent.bridge_store:X",
            surfaces=("moonmind.omnigent.bridge_store:X",),
        )


# ------------------------------------------------------------------- guards


def test_guard_passes_for_current_inventory() -> None:
    assert_retirement_guard()


def test_every_inventory_surface_still_resolves() -> None:
    from moonmind.omnigent.retirement_surfaces import surface_exists

    for path in RETIREMENT_INVENTORY:
        for ref in path.surfaces:
            assert surface_exists(ref), f"{path.path_id}: {ref}"


def test_guard_fails_when_a_retained_python_symbol_is_deleted() -> None:
    broken = (
        _record(
            machineCheckableRef=(
                "python:moonmind.omnigent.bridge_store:__DeletedStore__"
            ),
            surfaces=("python:moonmind.omnigent.bridge_store:__DeletedStore__",),
        ),
    )
    with pytest.raises(RetirementGuardError, match="no longer resolves"):
        assert_retirement_guard(broken)


def test_guard_fails_when_a_retained_compose_service_is_deleted() -> None:
    broken = (
        _record(
            machineCheckableRef="compose-service:omnigent-host-gone",
            surfaces=("compose-service:omnigent-host-gone",),
        ),
    )
    with pytest.raises(RetirementGuardError, match="no longer resolves"):
        assert_retirement_guard(broken)


def test_guard_fails_when_a_retained_startup_script_is_deleted() -> None:
    broken = (
        _record(
            machineCheckableRef="script:start-deleted-host.sh",
            surfaces=("script:start-deleted-host.sh",),
        ),
    )
    with pytest.raises(RetirementGuardError, match="no longer resolves"):
        assert_retirement_guard(broken)


def test_env_surface_evidence_excludes_the_inventory_itself(monkeypatch) -> None:
    """An ``env:`` row must not prove its own surface exists.

    ``legacy_retirement.py`` writes every inventoried variable into its own row,
    so including the inventory in the environment corpus let the guard pass
    after the real Compose and runtime handling for a retained variable had been
    deleted.
    """

    from moonmind.omnigent import retirement_surfaces

    variable = "OMNIGENT_ONLY_IN_THE_INVENTORY_IMAGE_REF"
    monkeypatch.setattr(
        retirement_surfaces, "COMPOSE_FILE", pathlib.Path("/nonexistent-compose.yaml")
    )
    retirement_surfaces.reset_surface_caches()
    try:
        assert retirement_surfaces.surface_exists(f"env:{variable}") is False
    finally:
        retirement_surfaces.reset_surface_caches()


def test_env_surface_evidence_ignores_comments_and_docstrings(monkeypatch) -> None:
    """Non-executable text is not a consumer of an environment identity."""

    from moonmind.omnigent import retirement_surfaces

    source = "\n".join(
        (
            '"""OMNIGENT_DOCSTRING_ONLY_REF is described here."""',
            "# OMNIGENT_COMMENT_ONLY_REF is mentioned here.",
            "import os",
            'value = os.environ["OMNIGENT_REALLY_CONSUMED_REF"]',
            "",
        )
    )
    executable = retirement_surfaces._executable_python_text(source)
    assert "OMNIGENT_DOCSTRING_ONLY_REF" not in executable
    assert "OMNIGENT_COMMENT_ONLY_REF" not in executable
    # An operand string is exactly how a variable is honored, so it is kept.
    assert "OMNIGENT_REALLY_CONSUMED_REF" in executable


def test_retained_env_surfaces_resolve_from_authoritative_consumers() -> None:
    """Every retained ``env:`` row still has a real consumer, not a self-reference."""

    from moonmind.omnigent.retirement_surfaces import surface_exists

    env_refs = [
        ref
        for path in RETIREMENT_INVENTORY
        if not path.removed
        for ref in path.surfaces
        if ref.startswith("env:")
    ]
    assert env_refs
    for ref in env_refs:
        assert surface_exists(ref), ref


def test_guard_fails_when_removed_path_has_unmet_criteria() -> None:
    removed = (
        _record(
            retirementClass=RetirementClass.REMOVED,
            replayDependency=False,
            applicableCriteria=frozenset(
                {
                    RetirementCriterion.NO_NEW_RECORDS_USE_IT,
                    RetirementCriterion.HISTORICAL_READS_AVAILABLE,
                }
            ),
        ),
    )
    with pytest.raises(RetirementGuardError, match="classified removed"):
        assert_retirement_guard(
            removed,
            passed_by_path={
                "omnigent.legacy.fixture": frozenset(
                    {RetirementCriterion.NO_NEW_RECORDS_USE_IT}
                )
            },
        )


def test_guard_allows_removed_path_when_all_criteria_pass() -> None:
    criteria = frozenset(
        {
            RetirementCriterion.NO_NEW_RECORDS_USE_IT,
            RetirementCriterion.HISTORICAL_READS_AVAILABLE,
        }
    )
    removed = (
        _record(
            retirementClass=RetirementClass.REMOVED,
            replayDependency=False,
            applicableCriteria=criteria,
            # A removed implementation no longer has to resolve.
            machineCheckableRef="python:moonmind.omnigent.bridge_store:__Gone__",
            surfaces=("python:moonmind.omnigent.bridge_store:__Gone__",),
        ),
    )
    assert_retirement_guard(
        removed, passed_by_path={"omnigent.legacy.fixture": criteria}
    )


def test_evaluate_retirement_reports_unmet_criteria() -> None:
    path = RETIREMENT_INVENTORY[0]
    decision = evaluate_retirement(path, frozenset())
    assert decision.allowed is False
    assert set(decision.unmet_criteria) == set(path.applicable_criteria)

    decision_full = evaluate_retirement(path, path.applicable_criteria)
    assert decision_full.allowed is True
    assert decision_full.unmet_criteria == ()


# ------------------------------------------------------------- completeness


def test_inventory_covers_every_discovered_legacy_surface() -> None:
    assert_inventory_is_complete()


def test_discovery_finds_the_surfaces_the_issue_enumerates() -> None:
    discovered = discover_legacy_surfaces()
    for expected in (
        "realizer:codex-profile-bound@1",
        "runtime-strategy:codex_cli",
        "runtime-strategy:claude_code",
        "compose-service:omnigent-host-codex",
        "compose-service:omnigent-host-claude",
        "compose-profile:omnigent-host-codex",
        "script:start-codex-oauth-host.sh",
        "script:start-claude-oauth-host.sh",
        "env:OMNIGENT_HOST_IMAGE_REF",
        "env:OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    ):
        assert expected in discovered, expected


def test_unclassified_new_legacy_dependency_fails_ci() -> None:
    # Simulate registering a new legacy realizer with no retirement row.
    with pytest.raises(RetirementGuardError, match="no retirement row"):
        assert_inventory_is_complete(
            discovered=discover_legacy_surfaces() | {"realizer:codex-legacy@2"}
        )


def test_two_rows_cannot_claim_the_same_surface() -> None:
    duplicated = (
        *RETIREMENT_INVENTORY,
        _record(pathId="omnigent.legacy.duplicate"),
    )
    with pytest.raises(RetirementGuardError, match="more than one row"):
        assert_inventory_is_complete(duplicated)


# ------------------------------------------------------------- new admission


def test_profile_bound_admission_follows_the_code_owned_class() -> None:
    record = get_retirement_record("omnigent.legacy.profile_bound_realizer")
    # Today the class still admits: #3833 has not promoted the generic rows, and
    # the issue forbids stopping admission before qualified defaults exist.
    assert record.retirement_class is RetirementClass.ACTIVE_PRODUCT_PATH
    assert evaluate_new_admission(record).allowed is True

    disabled = record.model_copy(
        update={
            "retirement_class": RetirementClass.NEW_ADMISSION_DISABLED,
            "new_admission_source": "",
        }
    )
    decision = evaluate_new_admission(disabled)
    assert decision.allowed is False
    assert decision.reason_code == "new_admission_disabled:new_admission_disabled"


def test_rollback_only_admits_only_the_exact_allowlisted_generation() -> None:
    rollback_only = _record(
        retirementClass=RetirementClass.ROLLBACK_ONLY,
        newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
        rollbackDependency=True,
        rollbackGenerations=frozenset({"gen-7"}),
        replayDependency=False,
    )
    assert evaluate_new_admission(rollback_only).reason_code == (
        "rollback_generation_required"
    )
    # Exact membership only: a prefix of the allowlisted generation is rejected.
    assert (
        evaluate_new_admission(rollback_only, rollback_generation="gen-").reason_code
        == "rollback_generation_not_allowlisted"
    )
    # The generation is a single global string, so it is necessary but not
    # sufficient: without a scoped exercise decision one allowlisted generation
    # would re-admit every scope using the path.
    assert (
        evaluate_new_admission(rollback_only, rollback_generation="gen-7").reason_code
        == "rollback_exercise_evidence_required"
    )
    unsatisfied = evaluate_new_admission(
        rollback_only,
        rollback_generation="gen-7",
        rollback_exercise=RollbackExerciseDecision(
            retirementPathId=rollback_only.path_id,
            satisfied=False,
            reasonCode="rollback_evidence_expired",
        ),
    )
    assert unsatisfied.allowed is False
    assert unsatisfied.reason_code == (
        "rollback_exercise_unsatisfied:rollback_evidence_expired"
    )
    # Evidence recorded for a different row never re-admits this one.
    other_row = evaluate_new_admission(
        rollback_only,
        rollback_generation="gen-7",
        rollback_exercise=RollbackExerciseDecision(
            retirementPathId="omnigent.legacy.other",
            satisfied=True,
            reasonCode="rollback_exercise_recorded",
        ),
    )
    assert other_row.allowed is False
    assert other_row.reason_code == "rollback_exercise_scope_mismatch"

    permitted = evaluate_new_admission(
        rollback_only,
        rollback_generation="gen-7",
        rollback_exercise=RollbackExerciseDecision(
            retirementPathId=rollback_only.path_id,
            satisfied=True,
            reasonCode="rollback_exercise_recorded",
        ),
    )
    assert permitted.allowed is True
    assert permitted.reason_code == "rollback_generation_permitted"


def test_assert_new_admission_raises_with_a_stable_reason_code() -> None:
    closed = (
        _record(pathId="omnigent.legacy.closed"),
    )
    with pytest.raises(LegacyAdmissionRejected) as excinfo:
        assert_new_admission_allowed("omnigent.legacy.closed", inventory=closed)
    assert excinfo.value.reason_code == "new_admission_disabled:temporal_replay_only"
    assert excinfo.value.path_id == "omnigent.legacy.closed"


def test_canonical_surfaces_are_never_subject_to_retirement_admission() -> None:
    assert assert_surface_admits_new_work("realizer:generic-omnigent-host@1") is None


def test_code_owned_retirement_state_matches_product_selection_state() -> None:
    """A realizer's registry deprecation must match its retirement class."""

    from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS

    for ref, entry in KNOWN_REALIZERS.items():
        record = None
        for path in RETIREMENT_INVENTORY:
            if f"realizer:{ref}" in path.surfaces:
                record = path
                break
        if record is None:
            # The canonical generic realizer carries no retirement row.
            assert entry["deprecated"] is False, ref
            continue
        assert entry["deprecated"] is not record.admits_new_work, (
            f"realizer {ref} is registered deprecated={entry['deprecated']} but "
            f"its retirement class is {record.retirement_class.value}"
        )


def test_runtime_selection_consults_the_retirement_class(monkeypatch) -> None:
    from moonmind.omnigent import legacy_retirement
    from moonmind.omnigent.cutover import CutoverPhase, select_runtime

    record = get_retirement_record("omnigent.legacy.direct_codex_launch")
    closed = record.model_copy(
        update={
            "retirement_class": RetirementClass.NEW_ADMISSION_DISABLED,
            "new_admission_source": "",
        }
    )
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_SURFACE, "runtime-strategy:codex_cli", closed
    )
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_ID,
        "omnigent.legacy.direct_codex_launch",
        closed,
    )

    with pytest.raises(ValueError, match="runtime_new_admission_disabled"):
        select_runtime(
            authored_runtime="codex_cli",
            configured_default="codex_cli",
            phase=CutoverPhase.OPT_IN,
        )
    # Omnigent remains selectable: closing legacy admission never blocks the
    # canonical destination.
    assert (
        select_runtime(
            authored_runtime="omnigent",
            configured_default="codex_cli",
            phase=CutoverPhase.OPT_IN,
        ).runtime_id
        == "omnigent"
    )


def test_legacy_rollback_generation_requires_the_explicit_control() -> None:
    class _Flags:
        omnigent_session_supervisor_rollback_mode = "none"
        omnigent_session_supervisor_generation = "gen-7"

    assert legacy_rollback_generation_from_settings(_Flags()) is None

    class _RollbackFlags(_Flags):
        omnigent_session_supervisor_rollback_mode = "revert_default_to_legacy"

    assert legacy_rollback_generation_from_settings(_RollbackFlags()) == "gen-7"


# --------------------------------------------------------- removal eligibility


def _all_criteria(path: LegacyPathRecord) -> frozenset[RetirementCriterion]:
    return frozenset(path.applicable_criteria)


def _drained(path: LegacyPathRecord) -> frozenset[ActiveOwnerKind]:
    return frozenset(path.active_resource_dependencies)


def _closed_windows() -> RetentionWindows:
    return RetentionWindows(
        replayWindowOpen=False,
        historicalReadWindowOpen=False,
        rollbackWindowOpen=False,
        rollbackExerciseRecorded=True,
    )


def test_removal_blocked_while_active_leases_or_hosts_remain() -> None:
    path = get_retirement_record("omnigent.legacy.profile_bound_execution")
    closed = path.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    # Everything drains except the Provider Profile lease and host lease.
    drained = _drained(closed) - {
        ActiveOwnerKind.PROVIDER_PROFILE_LEASE,
        ActiveOwnerKind.HOST_BINDING_OR_LEASE,
    }
    eligibility = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.LAUNCH_ONLY_CODE,
        drained_kinds=drained,
        passed_criteria=_all_criteria(closed),
        retention=_closed_windows(),
    )
    assert eligibility.eligible is False
    assert "active_owner:provider_profile_lease" in eligibility.blockers
    assert "active_owner:host_binding_or_lease" in eligibility.blockers

    fully_drained = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.LAUNCH_ONLY_CODE,
        drained_kinds=_drained(closed),
        passed_criteria=_all_criteria(closed),
        retention=_closed_windows(),
    )
    assert fully_drained.eligible is True, fully_drained.blockers


def test_cleanup_only_path_blocks_removal_until_janitor_authority_drains() -> None:
    path = get_retirement_record("omnigent.legacy.oauth_host_janitor")
    assert path.retirement_class is RetirementClass.CLEANUP_ONLY
    eligibility = evaluate_removal_eligibility(
        path,
        stage=RemovalStage.OAUTH_HOST_ORCHESTRATION,
        drained_kinds=_drained(path)
        - {ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR},
        passed_criteria=_all_criteria(path),
        retention=_closed_windows(),
    )
    assert eligibility.eligible is False
    assert "active_owner:incomplete_cleanup_or_janitor" in eligibility.blockers


def test_removal_blocked_while_replay_history_or_rollback_windows_are_open() -> None:
    path = get_retirement_record("omnigent.legacy.direct_codex_launch")
    closed = path.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    eligibility = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.PRODUCT_SELECTORS,
        drained_kinds=_drained(closed),
        passed_criteria=_all_criteria(closed),
        retention=RetentionWindows(),
    )
    assert eligibility.eligible is False
    assert "replay_window_open" in eligibility.blockers
    assert "historical_read_window_open" in eligibility.blockers
    assert "rollback_window_open" in eligibility.blockers
    assert "rollback_exercise_not_recorded" in eligibility.blockers


def test_removal_blocked_while_the_class_still_admits_new_work() -> None:
    path = get_retirement_record("omnigent.legacy.profile_bound_realizer")
    eligibility = evaluate_removal_eligibility(
        path,
        stage=RemovalStage.PRODUCT_SELECTORS,
        drained_kinds=_drained(path),
        passed_criteria=_all_criteria(path),
        retention=_closed_windows(),
    )
    assert eligibility.eligible is False
    assert "still_admits_new_work:active_product_path" in eligibility.blockers


def test_removal_blocked_while_retirement_criteria_are_unmet() -> None:
    path = get_retirement_record("omnigent.legacy.profile_bound_execution")
    closed = path.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    eligibility = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.LAUNCH_ONLY_CODE,
        drained_kinds=_drained(closed),
        passed_criteria=frozenset(),
        retention=_closed_windows(),
    )
    assert eligibility.eligible is False
    assert any(b.startswith("unmet_criterion:") for b in eligibility.blockers)


def test_removal_stage_ordering_is_enforced() -> None:
    """A historical reader cannot be deleted in a product-selector removal."""

    path = get_retirement_record("omnigent.legacy.bridge_persistence")
    closed = path.model_copy(
        update={
            "retirement_class": RetirementClass.HISTORICAL_READ_ONLY,
            "new_admission_source": "",
        }
    )
    early = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.PRODUCT_SELECTORS,
        drained_kinds=_drained(closed),
        passed_criteria=_all_criteria(closed),
        retention=_closed_windows(),
    )
    assert early.eligible is False
    assert any(b.startswith("stage_too_early:") for b in early.blockers)

    at_stage = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.HISTORICAL_READERS,
        drained_kinds=_drained(closed),
        passed_criteria=_all_criteria(closed),
        retention=_closed_windows(),
    )
    assert at_stage.eligible is True, at_stage.blockers


def test_removal_stages_cover_the_nine_ordered_stages() -> None:
    assert [stage.value for stage in RemovalStage] == list(range(1, 10))
    # Every stage a row cites must be one of the ordered stages, and the
    # inventory must actually span the staged convergence rather than collapsing
    # into one unreviewable removal.
    used = {path.earliest_removal_stage for path in RETIREMENT_INVENTORY}
    assert len(used) >= 6, sorted(stage.name for stage in used)


def test_direct_and_profile_bound_generations_are_independently_decided() -> None:
    """Direct Codex, direct Claude, and profile-bound retire separately."""

    by_generation: dict[RuntimeGeneration, list[LegacyPathRecord]] = {}
    for path in RETIREMENT_INVENTORY:
        by_generation.setdefault(path.generation, []).append(path)
    for generation in (
        RuntimeGeneration.DIRECT_CODEX,
        RuntimeGeneration.DIRECT_CLAUDE,
        RuntimeGeneration.CODEX_PROFILE_BOUND,
    ):
        assert by_generation.get(generation), generation.value

    # Closing direct Claude admission leaves direct Codex and profile-bound
    # untouched: no generation is forced to retire because another one did.
    claude = [
        path
        for path in by_generation[RuntimeGeneration.DIRECT_CLAUDE]
        if path.admits_new_work
    ]
    assert claude
    closed = tuple(
        path.model_copy(
            update={
                "retirement_class": RetirementClass.NEW_ADMISSION_DISABLED,
                "new_admission_source": "",
            }
        )
        for path in claude
    )
    for path in closed:
        assert evaluate_new_admission(path).allowed is False
    for path in by_generation[RuntimeGeneration.DIRECT_CODEX]:
        if path.admits_new_work:
            assert evaluate_new_admission(path).allowed is True
    for path in by_generation[RuntimeGeneration.CODEX_PROFILE_BOUND]:
        if path.admits_new_work:
            assert evaluate_new_admission(path).allowed is True


# ------------------------------------------------------------------ drain


def test_drain_evidence_is_fail_closed_without_observations() -> None:
    path = get_retirement_record("omnigent.legacy.oauth_host_runtime")
    evidence = build_drain_evidence(path, [], now=NOW)
    assert evidence.fully_drained is False
    assert set(evidence.missing_kinds) == path.active_resource_dependencies
    eligibility = evaluate_removal_eligibility(
        path.model_copy(
            update={
                "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
                "new_admission_source": "",
            }
        ),
        stage=RemovalStage.OAUTH_HOST_ORCHESTRATION,
        drained_kinds=evidence.drained_kinds,
        passed_criteria=_all_criteria(path),
        retention=_closed_windows(),
    )
    assert eligibility.eligible is False


def test_drain_evidence_rejects_stale_and_failed_probes() -> None:
    from moonmind.omnigent.retirement_drain import ActiveOwnerObservation

    path = get_retirement_record("omnigent.legacy.oauth_host_janitor")
    kinds = sorted(path.active_resource_dependencies, key=lambda k: k.value)
    observations = [
        ActiveOwnerObservation(
            kind=kinds[0],
            activeCount=0,
            probeRef="host.lease.scan",
            observedAt=NOW - timedelta(days=2),
        ),
        ActiveOwnerObservation(
            kind=kinds[1],
            activeCount=0,
            probeRef="janitor.queue.scan",
            observedAt=NOW,
            probeSucceeded=False,
        ),
        ActiveOwnerObservation(
            kind=kinds[2],
            activeCount=0,
            probeRef="static.host.scan",
            observedAt=NOW,
        ),
    ]
    evidence = build_drain_evidence(path, observations, now=NOW)
    assert evidence.stale_kinds == (kinds[0],)
    assert evidence.failed_kinds == (kinds[1],)
    assert evidence.drained_kinds == frozenset({kinds[2]})
    assert evidence.fully_drained is False


def test_drain_evidence_probe_refs_stay_operator_safe() -> None:
    from moonmind.omnigent.retirement_drain import ActiveOwnerObservation

    with pytest.raises(ValueError, match="operator-safe"):
        ActiveOwnerObservation(
            kind=ActiveOwnerKind.PROVIDER_PROFILE_LEASE,
            activeCount=0,
            probeRef="provider-session-8f3a1b2c-secret-token",
            observedAt=NOW,
        )


def test_drain_evidence_drains_only_declared_dependency_kinds() -> None:
    from moonmind.omnigent.retirement_drain import ActiveOwnerObservation

    path = get_retirement_record("omnigent.legacy.pi_host_image_alias")
    undeclared = ActiveOwnerObservation(
        kind=ActiveOwnerKind.PENDING_PUBLICATION,
        activeCount=0,
        probeRef="publication.scan",
        observedAt=NOW,
    )
    declared = [
        ActiveOwnerObservation(
            kind=kind, activeCount=0, probeRef="host.scan", observedAt=NOW
        )
        for kind in path.active_resource_dependencies
    ]
    evidence = build_drain_evidence(path, [undeclared, *declared], now=NOW)
    assert evidence.drained_kinds == path.active_resource_dependencies
    assert ActiveOwnerKind.PENDING_PUBLICATION not in evidence.drained_kinds


def test_a_newer_zero_count_probe_never_overwrites_another_blocker() -> None:
    """Every probe that observed a kind must agree it is drained.

    ``collect_drain_evidence`` runs every probe for every declared kind, so a
    kind routinely carries several observations. Reducing them to the newest one
    let a later zero-count success discard another authority's positive count or
    failure and mark the kind drained.
    """

    from moonmind.omnigent.retirement_drain import ActiveOwnerObservation

    path = get_retirement_record("omnigent.legacy.oauth_host_janitor")
    kinds = sorted(path.active_resource_dependencies, key=lambda k: k.value)
    active_kind, failed_kind, stale_kind = kinds[0], kinds[1], kinds[2]
    observations = [
        # One authority still reports an owner; a newer zero-count success from
        # a second probe must not clear it.
        ActiveOwnerObservation(
            kind=active_kind,
            activeCount=3,
            probeRef="host.lease.scan",
            observedAt=NOW - timedelta(minutes=5),
        ),
        ActiveOwnerObservation(
            kind=active_kind,
            activeCount=0,
            probeRef="janitor.queue.scan",
            observedAt=NOW,
        ),
        # One probe could not be checked at all.
        ActiveOwnerObservation(
            kind=failed_kind,
            activeCount=0,
            probeRef="probe.unavailable",
            observedAt=NOW - timedelta(minutes=5),
            probeSucceeded=False,
        ),
        ActiveOwnerObservation(
            kind=failed_kind,
            activeCount=0,
            probeRef="static.host.scan",
            observedAt=NOW,
        ),
        # One probe's evidence is too old to prove present absence.
        ActiveOwnerObservation(
            kind=stale_kind,
            activeCount=0,
            probeRef="host.lease.scan",
            observedAt=NOW - timedelta(days=2),
        ),
        ActiveOwnerObservation(
            kind=stale_kind,
            activeCount=0,
            probeRef="janitor.queue.scan",
            observedAt=NOW,
        ),
    ]
    evidence = build_drain_evidence(path, observations, now=NOW)
    assert evidence.drained_kinds == frozenset()
    assert set(evidence.blocking_kinds) == {active_kind, failed_kind, stale_kind}
    assert evidence.failed_kinds == (failed_kind,)
    assert evidence.stale_kinds == (stale_kind,)
    assert evidence.fully_drained is False


def test_drain_requires_every_probe_to_report_zero() -> None:
    from moonmind.omnigent.retirement_drain import ActiveOwnerObservation

    path = get_retirement_record("omnigent.legacy.pi_host_image_alias")
    observations = [
        ActiveOwnerObservation(
            kind=kind, activeCount=0, probeRef=ref, observedAt=NOW
        )
        for kind in path.active_resource_dependencies
        for ref in ("host.scan", "lease.scan")
    ]
    evidence = build_drain_evidence(path, observations, now=NOW)
    assert evidence.drained_kinds == path.active_resource_dependencies
    assert evidence.fully_drained is True


@pytest.mark.asyncio
async def test_collect_drain_evidence_keeps_a_positive_count_from_any_probe() -> None:
    """The real collector runs every probe for every kind."""

    from moonmind.omnigent.retirement_drain import (
        ActiveOwnerObservation,
        collect_drain_evidence,
    )

    path = get_retirement_record("omnigent.legacy.pi_host_image_alias")

    class _Busy:
        async def observe(self, path, kind) -> ActiveOwnerObservation:
            return ActiveOwnerObservation(
                kind=kind, activeCount=1, probeRef="host.scan", observedAt=NOW
            )

    class _Idle:
        async def observe(self, path, kind) -> ActiveOwnerObservation:
            return ActiveOwnerObservation(
                kind=kind, activeCount=0, probeRef="lease.scan", observedAt=NOW
            )

    evidence = await collect_drain_evidence(path, [_Busy(), _Idle()], now=NOW)
    assert evidence.drained_kinds == frozenset()
    assert evidence.fully_drained is False


# ---------------------------------------------------- removed-row guard


def test_removed_row_requires_complete_removal_evidence() -> None:
    """Passing criteria alone must not clear a row classified ``removed``.

    The guard delegates to the staged removal evaluation, so an active owner or
    an open replay/rollback window blocks the row even when every criterion the
    row declares is asserted as passing.
    """

    path = get_retirement_record("omnigent.legacy.oauth_host_runtime")
    removed = path.model_copy(
        update={
            "retirement_class": RetirementClass.REMOVED,
            "new_admission_source": "",
        }
    )
    with pytest.raises(RetirementGuardError) as excinfo:
        assert_retirement_guard(
            (removed,), passed_by_path={removed.path_id: _all_criteria(path)}
        )
    message = str(excinfo.value)
    assert "removal evidence is incomplete" in message
    assert any(
        f"active_owner:{kind.value}" in message
        for kind in path.active_resource_dependencies
    )


def test_removed_row_passes_with_complete_removal_evidence() -> None:
    path = get_retirement_record("omnigent.legacy.oauth_host_runtime")
    removed = path.model_copy(
        update={
            "retirement_class": RetirementClass.REMOVED,
            "new_admission_source": "",
        }
    )
    assert_retirement_guard(
        (removed,),
        passed_by_path={removed.path_id: _all_criteria(path)},
        drained_by_path={removed.path_id: path.active_resource_dependencies},
        retention_by_path={removed.path_id: _closed_windows()},
    )


def test_removed_row_blocks_a_stage_earlier_than_its_earliest() -> None:
    path = get_retirement_record("omnigent.legacy.oauth_host_runtime")
    removed = path.model_copy(
        update={
            "retirement_class": RetirementClass.REMOVED,
            "new_admission_source": "",
        }
    )
    with pytest.raises(RetirementGuardError, match="stage_too_early"):
        assert_retirement_guard(
            (removed,),
            passed_by_path={removed.path_id: _all_criteria(path)},
            drained_by_path={removed.path_id: path.active_resource_dependencies},
            retention_by_path={removed.path_id: _closed_windows()},
            removal_stage_by_path={removed.path_id: RemovalStage.PRODUCT_SELECTORS},
        )


# --------------------------------------------------------------- rollback


def _scope(**overrides: str) -> RollbackScope:
    payload = {
        "agentProfileRef": "profile@3",
        "hostClassRef": "codex-oauth-host@1",
        "materializerRef": "codex-oauth-home@1",
        "executionRealizerRef": "codex-profile-bound@1",
        "modelQualifiedId": "gpt-5-codex",
        "launchPolicyRef": "codex-static@1",
        "hostMode": "static",
        "architecture": "amd64",
        "ownerCohort": "internal",
    }
    payload.update(overrides)
    return RollbackScope(**payload)  # type: ignore[arg-type]


def _exercise(**overrides: object) -> RollbackExerciseRecord:
    payload: dict[str, object] = {
        "retirementPathId": "omnigent.legacy.profile_bound_realizer",
        "scope": _scope(),
        "exercisedAt": NOW - timedelta(days=1),
        "evidenceRef": "artifact://rollback/exercise-1",
        "succeeded": True,
    }
    payload.update(overrides)
    return RollbackExerciseRecord(**payload)  # type: ignore[arg-type]


def test_rollback_exercise_requires_an_exact_scope_match() -> None:
    decision = evaluate_rollback_exercise(
        retirement_path_id="omnigent.legacy.profile_bound_realizer",
        scope=_scope(),
        records=[_exercise()],
        now=NOW,
    )
    assert decision.satisfied is True

    mismatched = evaluate_rollback_exercise(
        retirement_path_id="omnigent.legacy.profile_bound_realizer",
        scope=_scope(hostMode="on_demand"),
        records=[_exercise()],
        now=NOW,
    )
    assert mismatched.satisfied is False
    assert mismatched.reason_code == "rollback_scope_mismatch"


def test_rollback_exercise_expires() -> None:
    decision = evaluate_rollback_exercise(
        retirement_path_id="omnigent.legacy.profile_bound_realizer",
        scope=_scope(),
        records=[_exercise(exercisedAt=NOW - timedelta(days=90))],
        now=NOW,
    )
    assert decision.satisfied is False
    assert decision.reason_code == "rollback_evidence_expired"


def test_rollback_exercise_that_touched_existing_work_is_not_evidence() -> None:
    decision = evaluate_rollback_exercise(
        retirement_path_id="omnigent.legacy.profile_bound_realizer",
        scope=_scope(),
        records=[_exercise(futureAdmissionOnly=False)],
        now=NOW,
    )
    assert decision.satisfied is False
    assert decision.reason_code == "rollback_exercise_touched_existing_work"


def test_rollback_scope_rejects_a_blank_dimension() -> None:
    with pytest.raises(ValueError, match="exact value"):
        _scope(ownerCohort="  ")


def test_rollback_exercise_is_required_before_removal_eligibility() -> None:
    path = get_retirement_record("omnigent.legacy.profile_bound_realizer")
    closed = path.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    decision = evaluate_rollback_exercise(
        retirement_path_id=path.path_id,
        scope=_scope(),
        records=[],
        now=NOW,
    )
    eligibility = evaluate_removal_eligibility(
        closed,
        stage=RemovalStage.PRODUCT_SELECTORS,
        drained_kinds=_drained(closed),
        passed_criteria=_all_criteria(closed),
        retention=RetentionWindows(
            replayWindowOpen=False,
            historicalReadWindowOpen=False,
            rollbackWindowOpen=False,
            rollbackExerciseRecorded=decision.satisfied,
        ),
    )
    assert eligibility.eligible is False
    assert "rollback_exercise_not_recorded" in eligibility.blockers


# --------------------------------------------------- obsolete configuration


def test_current_configuration_is_accepted() -> None:
    assert assert_obsolete_configuration({"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}) == ()


def test_obsolete_configuration_fails_with_an_actionable_message() -> None:
    deprecated = (
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_REF",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            deprecated=True,
            guidance="Repin the shared image digest before the next release.",
        ),
    )
    warnings = assert_obsolete_configuration(
        {"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}, configuration=deprecated
    )
    assert warnings and "OMNIGENT_SHARED_HOST_IMAGE_REF" in warnings[0]
    assert "omnigent.legacy.host_image_variable_alias" in warnings[0]

    removed = (deprecated[0].model_copy(update={"removed": True}),)
    with pytest.raises(ObsoleteConfigurationError, match="no longer honored"):
        assert_obsolete_configuration(
            {"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}, configuration=removed
        )
    # An unset obsolete variable never fails startup.
    assert (
        assert_obsolete_configuration({}, configuration=removed) == ()
    )


def test_obsolete_configuration_rows_name_real_retirement_paths() -> None:
    known = {path.path_id for path in RETIREMENT_INVENTORY}
    for entry in OBSOLETE_CONFIGURATION:
        assert entry.retirement_path_id in known, entry.variable
        assert entry.replacement.strip(), entry.variable


def test_obsolete_configuration_cannot_skip_its_deprecation_window() -> None:
    with pytest.raises(ValueError, match="deprecation window"):
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_REF",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            removed=True,
        )


# ------------------------------------------------------------ rollout flags


def test_temporary_flags_all_have_retirement_trigger() -> None:
    assert TEMPORARY_ROLLOUT_FLAGS
    assert_temporary_flags_have_retirement()


def test_temporary_flag_without_trigger_is_rejected() -> None:
    with pytest.raises(RetirementGuardError):
        assert_temporary_flags_have_retirement(
            {"omnigent_session_supervisor_enabled": ""}
        )


def test_temporary_flags_cover_supervisor_settings_fields() -> None:
    # The supervisor rollout flags must be registered as temporary so they can
    # never silently become a permanent alternate architecture.
    from moonmind.config.settings import FeatureFlagsSettings

    model_fields = set(FeatureFlagsSettings.model_fields)
    for flag in TEMPORARY_ROLLOUT_FLAGS:
        assert flag in model_fields


# --------------------------------------------------------- staged removal PRs


def test_removal_plan_cites_rows_and_guards_and_reports_blockers() -> None:
    from moonmind.omnigent.legacy_retirement import (
        RemovalPlan,
        evaluate_removal_plan,
    )

    plan = RemovalPlan(
        stage=RemovalStage.PRODUCT_SELECTORS,
        pathIds=(
            "omnigent.legacy.profile_bound_realizer",
            "omnigent.legacy.direct_codex_launch",
        ),
    )
    report = evaluate_removal_plan(plan)
    # Nothing is removable today; the report is the actionable citation.
    assert report.allowed is False
    assert {e.path_id for e in report.blocked} == set(plan.path_ids)
    assert report.required_guard_tests
    for guard in report.required_guard_tests:
        assert "::" in guard


def test_removal_plan_allows_a_bounded_stage_once_every_guard_passes() -> None:
    from moonmind.omnigent.legacy_retirement import (
        RemovalPlan,
        evaluate_removal_plan,
    )

    path_id = "omnigent.legacy.oauth_host_runtime"
    record = get_retirement_record(path_id)
    closed = record.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    report = evaluate_removal_plan(
        RemovalPlan(
            stage=RemovalStage.OAUTH_HOST_ORCHESTRATION, pathIds=(path_id,)
        ),
        inventory=(closed,),
        drained_by_path={path_id: _drained(closed)},
        passed_by_path={path_id: _all_criteria(closed)},
        retention_by_path={path_id: _closed_windows()},
    )
    assert report.allowed is True, report.blocked
    assert report.eligible_path_ids == (path_id,)


def test_removal_plan_is_fail_closed_without_evidence() -> None:
    from moonmind.omnigent.legacy_retirement import (
        RemovalPlan,
        evaluate_removal_plan,
    )

    path_id = "omnigent.legacy.oauth_host_runtime"
    record = get_retirement_record(path_id)
    closed = record.model_copy(
        update={
            "retirement_class": RetirementClass.ACTIVE_EXECUTION_SUPPORT,
            "new_admission_source": "",
        }
    )
    # Same plan, no supplied drain/criteria/retention evidence at all.
    report = evaluate_removal_plan(
        RemovalPlan(
            stage=RemovalStage.OAUTH_HOST_ORCHESTRATION, pathIds=(path_id,)
        ),
        inventory=(closed,),
    )
    assert report.allowed is False


def test_removal_plan_rejects_an_empty_or_duplicated_plan() -> None:
    from moonmind.omnigent.legacy_retirement import RemovalPlan

    with pytest.raises(ValueError, match="at least one retirement row"):
        RemovalPlan(stage=RemovalStage.PRODUCT_SELECTORS, pathIds=())
    with pytest.raises(ValueError, match="not name a retirement row twice"):
        RemovalPlan(
            stage=RemovalStage.PRODUCT_SELECTORS,
            pathIds=(
                "omnigent.legacy.profile_bound_realizer",
                "omnigent.legacy.profile_bound_realizer",
            ),
        )


def test_removal_plan_rejects_an_unknown_retirement_row() -> None:
    from moonmind.omnigent.legacy_retirement import (
        RemovalPlan,
        evaluate_removal_plan,
    )

    with pytest.raises(RetirementGuardError, match="unknown retirement path"):
        evaluate_removal_plan(
            RemovalPlan(
                stage=RemovalStage.PRODUCT_SELECTORS,
                pathIds=("omnigent.legacy.not_a_row",),
            )
        )


def test_removal_plan_will_not_delete_a_later_stage_row_early() -> None:
    from moonmind.omnigent.legacy_retirement import (
        RemovalPlan,
        evaluate_removal_plan,
    )

    # ``bridge_persistence`` is a historical reader (stage 9). A stage-1
    # product-selector removal may not sweep it up.
    path_id = "omnigent.legacy.bridge_persistence"
    record = get_retirement_record(path_id)
    closed = record.model_copy(
        update={
            "retirement_class": RetirementClass.HISTORICAL_READ_ONLY,
            "new_admission_source": "",
        }
    )
    report = evaluate_removal_plan(
        RemovalPlan(stage=RemovalStage.PRODUCT_SELECTORS, pathIds=(path_id,)),
        inventory=(closed,),
        drained_by_path={path_id: _drained(closed)},
        passed_by_path={path_id: _all_criteria(closed)},
        retention_by_path={path_id: _closed_windows()},
    )
    assert report.allowed is False
    assert any(
        blocker.startswith("stage_too_early:")
        for eligibility in report.blocked
        for blocker in eligibility.blockers
    )
