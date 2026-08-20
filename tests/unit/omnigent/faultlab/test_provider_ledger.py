"""Programmable fake provider independently records at-most-once side effects.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 2).
"""

from __future__ import annotations

from moonmind.omnigent.faultlab.provider import ProgrammableFakeProvider, payload_digest
from moonmind.omnigent.faultlab.scenario import (
    LogicalOperation,
    ResponseBehavior,
    SideEffect,
)


def test_repeated_idempotency_key_performs_one_side_effect():
    provider = ProgrammableFakeProvider()
    first = provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"prompt": "p"},
        side_effect=SideEffect.ACCEPTED,
    )
    second = provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"prompt": "p"},
        side_effect=SideEffect.ACCEPTED,
    )
    assert first.duplicate is False
    assert second.duplicate is True
    assert provider.ledger.accepted_side_effect_count("k1") == 1
    assert provider.ledger.keys_with_multiple_side_effects() == []


def test_dropped_response_still_records_the_side_effect():
    """A lost response does not un-happen the server-side side effect."""

    provider = ProgrammableFakeProvider()
    result = provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"prompt": "p"},
        side_effect=SideEffect.ACCEPTED,
        response=ResponseBehavior.DROP,
    )
    assert result.delivered is False
    assert result.accepted is True
    # The side effect is in the independent ledger even though delivery failed.
    assert provider.ledger.accepted_side_effect_count("k1") == 1


def test_ledger_records_only_digests_not_payloads():
    provider = ProgrammableFakeProvider()
    provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"secret": "ghp_shouldNeverBeStored"},
        side_effect=SideEffect.ACCEPTED,
    )
    record = provider.ledger.requests[0]
    assert record.payload_digest.startswith("sha256:")
    assert "ghp_" not in record.payload_digest


def test_key_reuse_with_different_payload_flags_conflict():
    provider = ProgrammableFakeProvider()
    provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"a": 1},
        side_effect=SideEffect.ACCEPTED,
    )
    result = provider.call(
        LogicalOperation.SUBMIT_TURN,
        idempotency_key="k1",
        payload={"a": 2},
        side_effect=SideEffect.ACCEPTED,
    )
    assert result.conflict is True
    # Even on a conflicting body, no second side effect is performed.
    assert provider.ledger.accepted_side_effect_count("k1") == 1


def test_payload_digest_is_stable():
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})
