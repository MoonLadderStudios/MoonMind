from __future__ import annotations

import copy

import pytest

from moonmind.schemas.workflow_recovery_models import (
    deterministic_recovery_creation_key,
)
from moonmind.workflows.temporal.recovery_entry import (
    compile_recovery_entry_policy,
)
from tests.unit.schemas.test_workflow_recovery_models import _payload


def test_failed_step_entry_compiles_before_semantic_work() -> None:
    payload = _payload()

    policy = compile_recovery_entry_policy(
        payload, destination_workflow_id="recovery-workflow"
    )

    assert policy.target_kind == "failed_step"
    assert policy.continuation_phase == "rerun_failed_step"
    assert policy.run_semantic_work is True
    assert policy.publication_only is False
    assert policy.restoration_only is False
    assert policy.side_effect_disposition_ref == "artifact://side-effects"
    assert policy.execution_route == "failed_step"
    assert policy.requires_budget_authority is False
    assert policy.requires_side_effect_authority is True


def test_entry_rejects_destination_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="RECOVERY_DESTINATION_IDENTITY_MISMATCH"):
        compile_recovery_entry_policy(
            _payload(), destination_workflow_id="another-workflow"
        )


def test_entry_independently_readmits_frozen_contract() -> None:
    payload = copy.deepcopy(_payload())
    payload["sideEffectSafe"] = False

    with pytest.raises(ValueError, match="RECOVERY_SIDE_EFFECT_UNSAFE"):
        compile_recovery_entry_policy(
            payload, destination_workflow_id="recovery-workflow"
        )


@pytest.mark.parametrize(
    ("kind", "phase", "route", "semantic_work", "budget_authority"),
    [
        ("failed_step", "rerun_failed_step", "failed_step", True, False),
        ("control_stop", "continue_to_gate", "gate", True, False),
        ("control_stop", "continue_after_gate", "post_gate", True, False),
        ("control_stop", "continue_to_remediation", "remediation", True, True),
        ("publication", "resume_publication", "publication", False, False),
        ("restoration_failure", "retry_restoration", "restoration", False, False),
    ],
)
def test_entry_compiles_one_explicit_route_for_every_canonical_phase(
    kind: str,
    phase: str,
    route: str,
    semantic_work: bool,
    budget_authority: bool,
) -> None:
    payload = _payload(kind)
    payload["continuation"]["phase"] = phase
    payload["destination"]["creationKey"] = deterministic_recovery_creation_key(
        payload["source"]["workflowId"],
        payload["source"]["runId"],
        kind,
        payload["checkpoint"]["digest"],
        phase,
    )
    if kind == "control_stop":
        payload["continuation"]["newBudgetRef"] = "artifact://new-budget"

    policy = compile_recovery_entry_policy(
        payload, destination_workflow_id="recovery-workflow"
    )

    assert policy.execution_route == route
    assert policy.run_semantic_work is semantic_work
    assert policy.requires_budget_authority is budget_authority
    assert policy.requires_side_effect_authority is True
