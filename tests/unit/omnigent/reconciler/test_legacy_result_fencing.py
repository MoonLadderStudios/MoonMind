"""A delayed legacy result cannot mutate the generic replacement's authority.

Source issue: MoonLadderStudios/MoonMind#3835 (required tests, "New admission
and active drain").

Retirement moves execution ownership forward. A legacy Activity that was already
in flight when ownership advanced may still return afterwards. Its result must
not be able to reach through and mutate the authority the generic replacement now
holds — that would transfer a live provider side-effect owner without a fenced
handoff, which the rollback contract forbids absolutely.

The fence is the durable ``(revision, fencing_generation)`` pair: every command
the reducer emits carries the *durable* expectation, never an observation's, and
the command id embeds both. A decision computed under the old authority is
therefore not applicable once authority advances.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.session_supervisor_rollback import (
    SessionRollbackContext,
    resolve_rollback_effect,
)


@pytest.fixture
def advanced_authority(make_durable):
    """The same session before and after the generic replacement takes over.

    Both states are ready to submit a turn, so the reducer emits a real
    side-effect command in each — the shape a delayed legacy Activity result
    would try to reuse.
    """

    from moonmind.omnigent.reconciler import LeaseState

    provisioned = dict(
        profile_lease=LeaseState.HELD,
        host_lease=LeaseState.HELD,
        provider_session_attached=True,
        provider_session_id="provider-session-1",
        attempt_id="attempt-1",
    )
    legacy = make_durable(revision=7, fencing_generation=3, **provisioned)
    generic = make_durable(revision=8, fencing_generation=4, **provisioned)
    return legacy, generic


def test_delayed_legacy_command_is_not_applicable_after_authority_advances(
    make_intent, run, advanced_authority
) -> None:
    intent = make_intent()
    legacy, generic = advanced_authority

    delayed = run(intent, legacy)
    current = run(intent, generic)

    # The delayed decision still expects the authority it was computed under.
    assert delayed.expected_revision == 7
    assert delayed.expected_fencing_generation == 3
    assert current.expected_revision == 8
    assert current.expected_fencing_generation == 4

    # Applying the delayed decision would require the old expectation, which no
    # longer matches durable authority, so it cannot mutate the replacement.
    assert delayed.expected_fencing_generation != generic.fencing_generation
    assert delayed.expected_revision != generic.revision


def test_delayed_legacy_command_id_never_collides_with_the_replacement(
    make_intent, run, advanced_authority
) -> None:
    """At-most-once identity is scoped by generation and revision."""

    intent = make_intent()
    legacy, generic = advanced_authority

    delayed = run(intent, legacy)
    current = run(intent, generic)
    assert delayed.command is not None
    assert current.command is not None

    assert delayed.command.command_id != current.command.command_id
    assert f"g{legacy.fencing_generation}" in delayed.command.command_id
    assert f"g{generic.fencing_generation}" in current.command.command_id


def test_reducer_expectations_come_from_durable_state_not_observations(
    make_intent, make_ready_durable, make_obs, run
) -> None:
    """A legacy observation cannot raise its own authority into the decision."""

    intent = make_intent()
    durable = make_ready_durable(revision=8, fencing_generation=4)
    decision_without = run(intent, durable)
    decision_with = run(intent, durable, make_obs())

    for decision in (decision_without, decision_with):
        assert decision.expected_revision == durable.revision
        assert decision.expected_fencing_generation == durable.fencing_generation


def test_ownership_transfer_always_requires_a_fenced_handoff() -> None:
    """No rollback mode may move a live side-effect owner without fencing."""

    for mode in (
        "none",
        "disable_new_admission",
        "disable_new_selection",
        "chat_read_only",
        "revert_default_to_legacy",
        "complete_stop",
    ):
        effect = resolve_rollback_effect(
            mode=mode,  # type: ignore[arg-type]
            context=SessionRollbackContext(isActive=True),
        )
        assert effect.fenced_handoff_required_for_ownership_transfer is True
        assert effect.mutates_active_session_authority is False
        assert effect.existing_session_continues_under_recorded_owner is True
