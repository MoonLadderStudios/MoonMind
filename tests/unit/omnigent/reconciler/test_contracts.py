"""Contract typing, versioning, and purity coverage for the reconciler.

Source issue: MoonLadderStudios/MoonMind#3702.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import moonmind.omnigent.reconciler.contracts as contracts_module
import moonmind.omnigent.reconciler.reducer as reducer_module
from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DecisionKind,
    DurableSessionState,
    ObservationSet,
    ProviderSessionObservation,
    ProviderStatusClass,
    ReasonCode,
    RECONCILER_CONTRACT_VERSION,
    classify_provider_status,
    reconcile,
)

FIXED_NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


# --- Typing / unknown-field / unknown-version compatibility policy ---------


def test_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CompiledSessionIntent(
            session_id="s",
            provider="omnigent",
            turn_prompt_digest="sha256:x",
            surprise="nope",
        )


@pytest.mark.parametrize("bad_version", ["v2", "V1", "", "1"])
def test_contract_rejects_unknown_schema_version(bad_version):
    with pytest.raises(ValidationError):
        CompiledSessionIntent(
            schema_version=bad_version,
            session_id="s",
            provider="omnigent",
            turn_prompt_digest="sha256:x",
        )


def test_reconcile_quarantines_bypassed_unknown_version():
    """A caller that bypasses validation must still fail closed at the boundary."""

    intent = CompiledSessionIntent(
        session_id="s", provider="omnigent", turn_prompt_digest="sha256:x"
    )
    # model_construct bypasses validation; simulate a future/unknown version.
    durable = DurableSessionState.model_construct(
        schema_version="v2",
        session_id="s",
        revision=1,
        owner_token="o",
        fencing_generation=1,
    )
    decision = reconcile(
        intent=intent,
        durable=durable,
        observations=ObservationSet(),
        now=FIXED_NOW,
    )
    assert decision.kind == DecisionKind.QUARANTINE_AMBIGUOUS_STATE
    assert decision.reason_code == ReasonCode.UNKNOWN_INPUT_VERSION
    assert decision.next_deadline is None


@pytest.mark.parametrize("bad_value", [0, -1])
def test_intent_rejects_non_positive_max_turn_attempts(bad_value):
    with pytest.raises(ValidationError):
        CompiledSessionIntent(
            session_id="s",
            provider="omnigent",
            turn_prompt_digest="sha256:x",
            max_turn_attempts=bad_value,
        )


@pytest.mark.parametrize("bad_value", [0, -5])
def test_intent_rejects_non_positive_reconcile_interval(bad_value):
    with pytest.raises(ValidationError):
        CompiledSessionIntent(
            session_id="s",
            provider="omnigent",
            turn_prompt_digest="sha256:x",
            reconcile_interval_seconds=bad_value,
        )


def test_snake_case_and_camel_case_construction_equivalent():
    snake = ProviderSessionObservation(observed_at=FIXED_NOW, raw_status="running")
    camel = ProviderSessionObservation.model_validate(
        {"observedAt": FIXED_NOW.isoformat(), "rawStatus": "running"}
    )
    assert snake == camel
    assert camel.model_dump(by_alias=True)["rawStatus"] == "running"


def test_contract_version_constant():
    assert RECONCILER_CONTRACT_VERSION == "v1"


# --- Provider status classification (invariant 6 vocabulary) --------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("created", ProviderStatusClass.ACTIVE),
        ("launching", ProviderStatusClass.ACTIVE),
        ("provisioning", ProviderStatusClass.ACTIVE),
        ("running", ProviderStatusClass.ACTIVE),
        ("waiting", ProviderStatusClass.ACTIVE),
        ("IN_PROGRESS", ProviderStatusClass.ACTIVE),
        ("idle", ProviderStatusClass.IDLE),
        ("completed", ProviderStatusClass.TERMINAL_SUCCESS),
        ("failed", ProviderStatusClass.TERMINAL_FAILURE),
        ("canceled", ProviderStatusClass.TERMINAL_CANCELLED),
        ("cancelled", ProviderStatusClass.TERMINAL_CANCELLED),
        # Timeouts are a system failure, kept distinct from user cancellation.
        ("timed_out", ProviderStatusClass.TERMINAL_FAILURE),
        ("timeout", ProviderStatusClass.TERMINAL_FAILURE),
        ("awaiting_approval", ProviderStatusClass.INTERVENTION),
        ("intervention_requested", ProviderStatusClass.INTERVENTION),
        ("Completed", ProviderStatusClass.TERMINAL_SUCCESS),
        ("  running ", ProviderStatusClass.ACTIVE),
        ("weird_new_status", ProviderStatusClass.UNKNOWN),
        ("", ProviderStatusClass.UNKNOWN),
    ],
)
def test_classify_provider_status(raw, expected):
    assert classify_provider_status(raw) == expected


# --- Purity: no infrastructure imports ------------------------------------

_FORBIDDEN_IMPORT_TOKENS = (
    "sqlalchemy",
    "temporalio",
    "docker",
    "requests",
    "httpx",
    "aiohttp",
    "logging",
    "boto3",
    "moonmind.workflows",
    "moonmind.omnigent.bridge_store",
    "moonmind.omnigent.execute",
    "moonmind.repositories",
    "moonmind.services",
)


@pytest.mark.parametrize(
    "module", [contracts_module, reducer_module]
)
def test_no_infrastructure_imports(module):
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    for name in imported:
        for token in _FORBIDDEN_IMPORT_TOKENS:
            assert token not in name, f"{module.__name__} must not import {name}"


def test_reconcile_does_not_mutate_inputs():
    intent = CompiledSessionIntent(
        session_id="s", provider="omnigent", turn_prompt_digest="sha256:x"
    )
    durable = DurableSessionState(
        session_id="s", revision=1, owner_token="o", fencing_generation=1
    )
    observations = ObservationSet()
    before = (
        intent.model_dump(),
        durable.model_dump(),
        observations.model_dump(),
    )
    reconcile(intent=intent, durable=durable, observations=observations, now=FIXED_NOW)
    after = (
        intent.model_dump(),
        durable.model_dump(),
        observations.model_dump(),
    )
    assert before == after


def test_every_decision_kind_reachable_has_stable_value():
    # The closed vocabulary must serialize to stable snake_case tokens.
    assert {k.value for k in DecisionKind} == {
        "no_op",
        "await_observation",
        "ensure_profile_lease",
        "ensure_host",
        "ensure_provider_session",
        "submit_turn",
        "record_provider_terminal",
        "synthesize_terminal_from_snapshot",
        "harvest_evidence",
        "begin_cleanup",
        "release_leases",
        "retry_transient_observation",
        "quarantine_ambiguous_state",
        "fail_nonretryable",
    }
