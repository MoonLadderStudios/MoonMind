from __future__ import annotations

import copy

import pytest

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
