"""Legacy retirement admission table + guard tests.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835,
MoonLadderStudios/MoonMind#3932.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.legacy_retirement import (
    OBSOLETE_CONFIGURATION,
    RETIREMENT_INVENTORY,
    TEMPORARY_ROLLOUT_FLAGS,
    LegacyAdmissionRejected,
    LegacyPathRecord,
    ObsoleteConfiguration,
    ObsoleteConfigurationError,
    RetirementClass,
    RetirementGuardError,
    assert_inventory_is_complete,
    assert_new_admission_allowed,
    assert_obsolete_configuration,
    assert_surface_admits_new_work,
    assert_temporary_flags_have_retirement,
    evaluate_new_admission,
    get_retirement_record,
)
from moonmind.omnigent.retirement_surfaces import discover_legacy_surfaces
from moonmind.omnigent.session_supervisor_rollback import (
    RollbackExerciseDecision,
    RollbackExerciseRecord,
    RollbackScope,
    evaluate_rollback_exercise,
    legacy_rollback_generation_from_settings,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _record(**overrides: object) -> LegacyPathRecord:
    payload: dict[str, object] = {
        "pathId": "omnigent.legacy.fixture",
        "owner": "omnigent-control-plane",
        "description": "Fixture row.",
        "retirementClass": RetirementClass.NEW_ADMISSION_DISABLED,
        "surfaces": (
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore",
        ),
    }
    payload.update(overrides)
    return LegacyPathRecord(**payload)  # type: ignore[arg-type]


# ------------------------------------------------------------------ records


def test_record_rejects_admission_source_on_a_closed_class() -> None:
    with pytest.raises(ValueError, match="does not admit new work"):
        _record(newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution")


def test_record_requires_admission_source_while_it_still_admits() -> None:
    with pytest.raises(ValueError, match="must be named"):
        _record(retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH)


def test_record_requires_rollback_allowlist_for_rollback_only() -> None:
    with pytest.raises(ValueError, match="exact permitted rollback generation"):
        _record(
            retirementClass=RetirementClass.ROLLBACK_ONLY,
            newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
        )


def test_record_rejects_a_surface_ref_without_a_scheme() -> None:
    with pytest.raises(ValueError):
        _record(surfaces=("moonmind.omnigent.bridge_store:X",))


# ------------------------------------------------------------ stale rows


@pytest.mark.parametrize(
    "surface",
    (
        "python:moonmind.omnigent.bridge_store:__DeletedStore__",
        "compose-service:omnigent-host-gone",
        "script:start-deleted-host.sh",
    ),
)
def test_a_row_whose_surface_is_gone_must_be_deleted_with_the_code(
    surface: str,
) -> None:
    # Deleting a legacy component deletes its admission row in the same change;
    # there is no "removed" class or removal evidence to keep it around.
    stale = (*RETIREMENT_INVENTORY, _record(surfaces=(surface,)))
    with pytest.raises(RetirementGuardError, match="no longer exist"):
        assert_inventory_is_complete(stale)


def test_two_rows_cannot_share_a_path_id() -> None:
    duplicated = (
        *RETIREMENT_INVENTORY,
        _record(pathId=RETIREMENT_INVENTORY[0].path_id),
    )
    with pytest.raises(RetirementGuardError, match="duplicate retirement row"):
        assert_inventory_is_complete(duplicated)


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
        _record(
            pathId="omnigent.legacy.duplicate",
            surfaces=RETIREMENT_INVENTORY[0].surfaces,
        ),
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
        rollbackGenerations=frozenset({"gen-7"}),
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
    closed = (_record(pathId="omnigent.legacy.closed"),)
    with pytest.raises(LegacyAdmissionRejected) as excinfo:
        assert_new_admission_allowed("omnigent.legacy.closed", inventory=closed)
    assert excinfo.value.reason_code == "new_admission_disabled:new_admission_disabled"
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


# --------------------------------------------------- obsolete configuration


def test_current_configuration_is_accepted() -> None:
    assert (
        assert_obsolete_configuration({"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}) == ()
    )


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
    assert assert_obsolete_configuration({}, configuration=removed) == ()


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
            {"omnigent_session_supervisor_rollback_mode": ""}
        )


def test_temporary_flags_cover_supervisor_settings_fields() -> None:
    # The supervisor rollout flags must be registered as temporary so they can
    # never silently become a permanent alternate architecture.
    from moonmind.config.settings import FeatureFlagsSettings

    model_fields = set(FeatureFlagsSettings.model_fields)
    for flag in TEMPORARY_ROLLOUT_FLAGS:
        assert flag in model_fields
