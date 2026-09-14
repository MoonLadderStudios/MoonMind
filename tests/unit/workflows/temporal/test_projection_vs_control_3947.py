"""MoonLadderStudios/MoonMind#3947 R5: projection scope is not a control allowlist.

Product visibility admits a type to product views; Pause, rerun, edit, and
continuation support stay governed by the existing action-capability owners.
These tests pin the conjunction: the product-visible type passes projection
while the capability matrix still denies an unsupported control. They also
pin the current historical-type interpretation (retired ManifestIngest rows
are never current product UserWorkflows) and the quiesce-enumeration binding
(running-UserWorkflow point-in-time set only).
"""

import pytest

from moonmind.workflows.temporal.hard_switch_cutover import RENAMED_USER_WORKFLOW_TYPE
from moonmind.workflows.temporal.remediation_actions import (
    RemediationCapabilityContext,
    remediation_action_capability,
)
from moonmind.workflows.temporal.workflow_registry import (
    WorkflowProjectionExcluded,
    product_workflow_types,
    require_product_projection,
    workflow_projection_scope,
    workflow_projection_scopes,
)


def test_product_visible_type_denied_unsupported_pause() -> None:
    # Product visibility admits the type to product views...
    require_product_projection("MoonMind.UserWorkflow")
    # ...but never implies control capability: Pause stays denied while the
    # exact target is ineligible, with a bounded reason from the capability
    # owner rather than the projection scope.
    capability = remediation_action_capability(
        "execution.pause",
        context=RemediationCapabilityContext(target_state_eligible=False),
    )
    assert capability["requestable"] is False
    assert "target_state_ineligible" in capability["blockedReasons"]


def test_product_visible_type_denied_policy_unlisted_action() -> None:
    # Even for a product-visible target, an action outside the persisted
    # target-policy intersection is not requestable: fail closed.
    require_product_projection("MoonMind.UserWorkflow")
    capability = remediation_action_capability(
        "execution.pause",
        context=RemediationCapabilityContext(policy_allowed_action_kinds=()),
    )
    assert capability["requestable"] is False
    assert "target_policy_denied" in capability["blockedReasons"]


def test_retired_manifest_ingest_is_never_a_current_product_workflow() -> None:
    # MoonLadderStudios/MoonMind#4192 retired the native ManifestIngest
    # product. Old rows stay readable as replay/drain evidence but are never
    # registered product types and must not be coerced into UserWorkflows.
    assert product_workflow_types() == ("MoonMind.UserWorkflow",)
    assert workflow_projection_scope("MoonMind.ManifestIngest") == "unknown"
    with pytest.raises(WorkflowProjectionExcluded) as failure:
        require_product_projection("MoonMind.ManifestIngest")
    assert failure.value.scope == "unknown"
    assert failure.value.code == "workflow_type_unknown"


def test_quiesce_enumeration_type_matches_only_product_scope() -> None:
    # System quiesce enumerates a running-UserWorkflow point-in-time set
    # (TemporalExecutionService batch Update path records the
    # ``WorkflowType="MoonMind.UserWorkflow"`` selection policy). That
    # enumeration type must stay exactly the product set: no operator or
    # excluded type may enter product views or the quiesce batch.
    assert RENAMED_USER_WORKFLOW_TYPE == "MoonMind.UserWorkflow"
    assert product_workflow_types() == (RENAMED_USER_WORKFLOW_TYPE,)
    scopes = workflow_projection_scopes()
    assert set(scopes) and RENAMED_USER_WORKFLOW_TYPE in scopes
    for name, scope in scopes.items():
        if name != RENAMED_USER_WORKFLOW_TYPE:
            assert scope in ("operator", "excluded")
