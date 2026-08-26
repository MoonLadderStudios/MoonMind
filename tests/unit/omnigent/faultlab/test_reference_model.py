"""The reference state machine is an independent lifecycle oracle.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 3).

The reference model must not simply call the production reconciler; these tests
pin its independence (no reducer import) and its hand-written transition rules.
"""

from __future__ import annotations

import inspect

import pytest

from moonmind.omnigent.faultlab import reference_model
from moonmind.omnigent.faultlab.reference_model import (
    IllegalTransitionError,
    ReferenceCommand,
    ReferenceModel,
    ReferencePhase,
)


def test_reference_model_does_not_import_the_production_reducer():
    """Independence is structural: the module never imports the reducer."""

    import_lines = [
        line.strip()
        for line in inspect.getsource(reference_model).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("reconciler" in line for line in import_lines)
    # The reducer's public function is not reachable from this module either.
    assert not hasattr(reference_model, "reconcile")


def test_expected_command_sequence_is_the_canonical_happy_path():
    model = ReferenceModel()
    assert model.expected_command_sequence() == (
        ReferenceCommand.ENSURE_PROFILE_LEASE,
        ReferenceCommand.ENSURE_HOST,
        ReferenceCommand.ENSURE_SESSION,
        ReferenceCommand.SUBMIT_TURN,
        ReferenceCommand.RECORD_TERMINAL,
        ReferenceCommand.HARVEST_EVIDENCE,
        ReferenceCommand.BEGIN_CLEANUP,
        ReferenceCommand.RELEASE_LEASES,
    )


def test_full_happy_path_reaches_closed():
    model = ReferenceModel()
    for command in model.expected_command_sequence():
        model.apply(command)
    assert model.is_closed()
    assert model.final_phase() == ReferencePhase.CLOSED
    assert model.terminal_outcome == "success"


def test_duplicate_submission_is_illegal():
    model = ReferenceModel()
    model.apply(ReferenceCommand.ENSURE_PROFILE_LEASE)
    model.apply(ReferenceCommand.ENSURE_HOST)
    model.apply(ReferenceCommand.ENSURE_SESSION)
    model.apply(ReferenceCommand.SUBMIT_TURN)
    with pytest.raises(IllegalTransitionError):
        model.apply(ReferenceCommand.SUBMIT_TURN)


def test_cleanup_before_evidence_is_illegal():
    model = ReferenceModel()
    for command in (
        ReferenceCommand.ENSURE_PROFILE_LEASE,
        ReferenceCommand.ENSURE_HOST,
        ReferenceCommand.ENSURE_SESSION,
        ReferenceCommand.SUBMIT_TURN,
        ReferenceCommand.RECORD_TERMINAL,
    ):
        model.apply(command)
    with pytest.raises(IllegalTransitionError):
        model.apply(ReferenceCommand.BEGIN_CLEANUP)


def test_release_before_cleanup_is_illegal():
    model = ReferenceModel()
    for command in (
        ReferenceCommand.ENSURE_PROFILE_LEASE,
        ReferenceCommand.ENSURE_HOST,
        ReferenceCommand.ENSURE_SESSION,
        ReferenceCommand.SUBMIT_TURN,
        ReferenceCommand.RECORD_TERMINAL,
        ReferenceCommand.HARVEST_EVIDENCE,
    ):
        model.apply(command)
    with pytest.raises(IllegalTransitionError):
        model.apply(ReferenceCommand.RELEASE_LEASES)


def test_desired_cancel_records_terminal_before_provisioning():
    model = ReferenceModel(desired_cancel=True)
    assert model.expected_command_sequence()[0] == ReferenceCommand.RECORD_TERMINAL
    model.apply(ReferenceCommand.RECORD_TERMINAL)
    assert model.terminal_outcome == "cancelled"
    with pytest.raises(IllegalTransitionError):
        model.apply(ReferenceCommand.SUBMIT_TURN)


def test_no_lease_run_expects_no_release():
    model = ReferenceModel(requires_profile_lease=False, requires_host=False)
    assert ReferenceCommand.RELEASE_LEASES not in model.expected_command_sequence()
