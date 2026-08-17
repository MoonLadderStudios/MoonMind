"""Compatibility-policy and contract tests for the lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Unknown intent / durable-state / observation / decision / reason-code versions
must follow an explicit fail (or quarantine) policy. Domain objects reject
unknown fields structurally and enforce the bounded-deadline invariant.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.reconciler import (
    CommandSpec,
    DecisionAction,
    ObservationSet,
    ReasonCode,
    ReconciliationDecision,
    UnknownSchemaVersionError,
    parse_reason_code,
)
from moonmind.omnigent.reconciler.versions import (
    DECISION_SCHEMA_VERSION,
    DURABLE_STATE_SCHEMA_VERSION,
    INTENT_SCHEMA_VERSION,
    OBSERVATION_SET_SCHEMA_VERSION,
)
from tests.helpers.omnigent_reconciler import make_durable, make_intent


def test_unknown_intent_version_fails():
    with pytest.raises(UnknownSchemaVersionError):
        make_intent(schema_version="moonmind.omnigent.reconciler.intent.v999")


def test_unknown_durable_version_fails():
    with pytest.raises(UnknownSchemaVersionError):
        make_durable(schema_version="durable.vX")


def test_unknown_observation_version_fails():
    with pytest.raises(UnknownSchemaVersionError):
        ObservationSet(schema_version="observations.vX")


def test_unknown_decision_version_fails():
    with pytest.raises(UnknownSchemaVersionError):
        ReconciliationDecision(
            action=DecisionAction.NO_OP,
            reason_codes=(ReasonCode.SESSION_ALREADY_CLOSED,),
            expected_revision=1,
            expected_fencing_generation=1,
            changes_product_visible_state=False,
            terminal=True,
            schema_version="decision.vX",
        )


def test_supported_versions_construct_cleanly():
    intent = make_intent(schema_version=INTENT_SCHEMA_VERSION)
    durable = make_durable(schema_version=DURABLE_STATE_SCHEMA_VERSION)
    obs = ObservationSet(schema_version=OBSERVATION_SET_SCHEMA_VERSION)
    assert intent.schema_version == INTENT_SCHEMA_VERSION
    assert durable.schema_version == DURABLE_STATE_SCHEMA_VERSION
    assert obs.schema_version == OBSERVATION_SET_SCHEMA_VERSION
    assert DECISION_SCHEMA_VERSION.endswith("decision.v1")


def test_unknown_fields_are_rejected_structurally():
    with pytest.raises(TypeError):
        make_intent(unexpected_field="boom")


def test_parse_reason_code_roundtrip_and_unknown_fails():
    assert parse_reason_code("need_host") is ReasonCode.NEED_HOST
    with pytest.raises(ValueError):
        parse_reason_code("totally_made_up_reason")


def test_nonterminal_decision_requires_bounded_next_step():
    with pytest.raises(ValueError):
        ReconciliationDecision(
            action=DecisionAction.AWAIT_OBSERVATION,
            reason_codes=(ReasonCode.NEED_HOST,),
            expected_revision=1,
            expected_fencing_generation=1,
            changes_product_visible_state=False,
            terminal=False,  # nonterminal but no deadline / wait authority
        )


def test_decision_requires_reason_code():
    with pytest.raises(ValueError):
        ReconciliationDecision(
            action=DecisionAction.NO_OP,
            reason_codes=(),
            expected_revision=1,
            expected_fencing_generation=1,
            changes_product_visible_state=False,
            terminal=True,
        )


def test_command_spec_identity_is_stable_value():
    a = CommandSpec(kind="submit_turn", command_id="s:1:submit_turn:x:a")
    b = CommandSpec(kind="submit_turn", command_id="s:1:submit_turn:x:a")
    assert a == b
