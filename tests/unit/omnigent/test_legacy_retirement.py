"""MoonLadderStudios/MoonMind#3712 legacy retirement inventory + guard tests."""

from __future__ import annotations

import pytest

from moonmind.omnigent.legacy_retirement import (
    RETIREMENT_INVENTORY,
    TEMPORARY_ROLLOUT_FLAGS,
    LegacyPathRecord,
    RetirementCriterion,
    RetirementGuardError,
    assert_retirement_guard,
    assert_temporary_flags_have_retirement,
    evaluate_retirement,
)


def test_inventory_is_nonempty_and_nothing_retired_yet() -> None:
    assert RETIREMENT_INVENTORY
    # This cohort has not proven replacement coverage; nothing may be retired.
    assert all(not path.retired for path in RETIREMENT_INVENTORY)


def test_guard_passes_for_current_inventory() -> None:
    # All machine-checkable refs resolve and nothing is retired.
    assert_retirement_guard()


def test_guard_fails_when_required_path_ref_is_deleted() -> None:
    broken = (
        LegacyPathRecord(
            pathId="omnigent.legacy.ghost",
            owner="omnigent-control-plane",
            description="A path whose module no longer exists.",
            machineCheckableRef="moonmind.omnigent.__does_not_exist__",
            applicableCriteria=frozenset({RetirementCriterion.HISTORICAL_READS_AVAILABLE}),
        ),
    )
    with pytest.raises(RetirementGuardError):
        assert_retirement_guard(broken)


def test_guard_fails_when_retired_path_has_unmet_criteria() -> None:
    retired = (
        LegacyPathRecord(
            pathId="omnigent.legacy.bridge_persistence",
            owner="omnigent-control-plane",
            description="Marked retired prematurely.",
            machineCheckableRef="moonmind.omnigent.bridge_store",
            applicableCriteria=frozenset(
                {
                    RetirementCriterion.NO_NEW_RECORDS_USE_IT,
                    RetirementCriterion.HISTORICAL_READS_AVAILABLE,
                }
            ),
            retired=True,
        ),
    )
    with pytest.raises(RetirementGuardError):
        assert_retirement_guard(
            retired,
            passed_by_path={
                "omnigent.legacy.bridge_persistence": frozenset(
                    {RetirementCriterion.NO_NEW_RECORDS_USE_IT}
                )
            },
        )


def test_guard_allows_retired_path_when_all_criteria_pass() -> None:
    criteria = frozenset(
        {
            RetirementCriterion.NO_NEW_RECORDS_USE_IT,
            RetirementCriterion.HISTORICAL_READS_AVAILABLE,
        }
    )
    retired = (
        LegacyPathRecord(
            pathId="omnigent.legacy.bridge_persistence",
            owner="omnigent-control-plane",
            description="Fully retired with evidence.",
            machineCheckableRef="moonmind.omnigent.bridge_store",
            applicableCriteria=criteria,
            retired=True,
        ),
    )
    # No exception: all applicable criteria passed.
    assert_retirement_guard(
        retired, passed_by_path={"omnigent.legacy.bridge_persistence": criteria}
    )


def test_evaluate_retirement_reports_unmet_criteria() -> None:
    path = RETIREMENT_INVENTORY[0]
    decision = evaluate_retirement(path, frozenset())
    assert decision.allowed is False
    assert set(decision.unmet_criteria) == set(path.applicable_criteria)

    decision_full = evaluate_retirement(path, path.applicable_criteria)
    assert decision_full.allowed is True
    assert decision_full.unmet_criteria == ()


def test_temporary_flags_all_have_retirement_trigger() -> None:
    assert TEMPORARY_ROLLOUT_FLAGS
    assert_temporary_flags_have_retirement()


def test_temporary_flag_without_trigger_is_rejected() -> None:
    with pytest.raises(RetirementGuardError):
        assert_temporary_flags_have_retirement({"omnigent_session_supervisor_enabled": ""})


def test_temporary_flags_cover_supervisor_settings_fields() -> None:
    # The supervisor rollout flags must be registered as temporary so they can
    # never silently become a permanent alternate architecture.
    from moonmind.config.settings import FeatureFlagsSettings

    model_fields = set(FeatureFlagsSettings.model_fields)
    for flag in TEMPORARY_ROLLOUT_FLAGS:
        assert flag in model_fields
