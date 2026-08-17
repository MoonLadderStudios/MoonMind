"""AC3: an independent reference state machine (not the production reconciler).

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

import inspect

from moonmind.omnigent.faultkit import reference_model
from moonmind.omnigent.faultkit.reference_model import (
    ReferenceModel,
    SessionState,
    TurnState,
    turn_transition_allowed,
)


def test_reference_model_does_not_import_or_call_the_reconciler() -> None:
    # The oracle must be independent of the system under test: it may mention the
    # reconciler in prose, but must not import or invoke it.
    source = inspect.getsource(reference_model)
    assert "import" in source  # sanity: we are reading real source
    assert "faultkit.reconciler" not in source
    assert "FaultKitReconciler" not in source
    assert "from moonmind.omnigent.faultkit import reconciler" not in source


def test_oracle_derives_terminal_from_provider_truth() -> None:
    model = ReferenceModel()
    model.observe_created_truth()
    model.observe_accept_truth()
    model.observe_snapshot_truth({"sessionState": "idle", "turnState": "completed"})
    view = model.finalize()
    assert view.turn_state is TurnState.COMPLETED
    assert view.session_state is SessionState.TERMINAL
    assert view.terminal_evidence_retained is True


def test_oracle_rejects_illegal_backward_turn_transition() -> None:
    model = ReferenceModel()
    model.observe_accept_truth()
    model.observe_snapshot_truth({"turnState": "completed"})
    # A later "running" truth must not roll the terminal turn backward.
    model.observe_snapshot_truth({"turnState": "running"})
    assert model.view.turn_state is TurnState.COMPLETED


def test_turn_transition_table_is_forward_only() -> None:
    assert turn_transition_allowed(TurnState.NONE, TurnState.ACCEPTED)
    assert turn_transition_allowed(TurnState.ACCEPTED, TurnState.COMPLETED)
    assert not turn_transition_allowed(TurnState.COMPLETED, TurnState.ACCEPTED)
    assert not turn_transition_allowed(TurnState.NONE, TurnState.COMPLETED)
