"""Legacy retirement admission table + guard tests.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835,
MoonLadderStudios/MoonMind#3932.
"""

from __future__ import annotations

import pathlib

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
from moonmind.omnigent.retirement_surfaces import (
    discover_legacy_surfaces,
    parse_surface_ref,
)


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


def test_record_rejects_the_retired_rollback_only_class() -> None:
    # A row either admits new work or refuses it; there is no rollback-gated
    # re-admission class.
    with pytest.raises(ValueError):
        _record(
            retirementClass="rollback_only",
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


def test_discovery_finds_every_inventoried_surface_it_derives() -> None:
    # Completeness alone passes when discovery silently finds nothing, so the
    # detector must still see every row surface of a kind it derives from the
    # repository. Deleting a legacy component deletes its row, not this test.
    derived_schemes = {
        "realizer",
        "runtime-strategy",
        "script",
        "compose-service",
        "compose-profile",
        "env",
    }
    discovered = discover_legacy_surfaces()
    missed = sorted(
        ref
        for path in RETIREMENT_INVENTORY
        for ref in path.surfaces
        if parse_surface_ref(ref)[0] in derived_schemes and ref not in discovered
    )
    assert missed == []


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
    # The realizer still provides required behavior, so it keeps admitting.
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


def test_admission_has_exactly_one_admitting_and_one_refusing_class() -> None:
    assert {member.value for member in RetirementClass} == {
        "active_product_path",
        "new_admission_disabled",
    }
    admitting = _record(
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
    )
    assert evaluate_new_admission(admitting).reason_code == "active_product_path"
    assert evaluate_new_admission(_record()).allowed is False


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


def test_every_retained_supervisor_flag_declares_its_retirement() -> None:
    # MoonLadderStudios/MoonMind#3935: a retained-lane flag that is not
    # registered here has no exit condition and can outlive the lane.
    from moonmind.config.settings import FeatureFlagsSettings

    supervisor_flags = {
        field
        for field in FeatureFlagsSettings.model_fields
        if field.startswith("omnigent_session_supervisor_")
    }
    assert supervisor_flags
    assert supervisor_flags <= set(TEMPORARY_ROLLOUT_FLAGS)
