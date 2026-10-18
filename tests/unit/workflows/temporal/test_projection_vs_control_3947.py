"""MoonLadderStudios/MoonMind#3947 R5: projection scope is not a control allowlist.

Product visibility admits a type to product views; Pause, rerun, edit, and
continuation support stay governed by the existing action-capability owners.
These tests pin the current historical-type interpretation (retired
ManifestIngest rows are readable but never current product UserWorkflows) and
the quiesce-enumeration binding (running-UserWorkflow point-in-time set only).
"""

import pytest

from moonmind.workflows.temporal.hard_switch_cutover import RENAMED_USER_WORKFLOW_TYPE
from moonmind.workflows.temporal.workflow_registry import (
    WorkflowProjectionExcluded,
    product_read_workflow_types,
    product_workflow_types,
    require_product_projection,
    workflow_projection_scope,
    workflow_projection_scopes,
)


def test_retired_manifest_ingest_is_never_a_current_product_workflow() -> None:
    # MoonLadderStudios/MoonMind#4192 retired the native ManifestIngest
    # product. Old rows stay readable through product read views (#4189) as
    # a historical scope, but the type is never registered, launchable, or
    # coerced into a UserWorkflow, and only readers opt in to admit it.
    assert product_workflow_types() == ("MoonMind.UserWorkflow",)
    assert "MoonMind.ManifestIngest" not in workflow_projection_scopes()
    assert workflow_projection_scope("MoonMind.ManifestIngest") == "historical"
    assert product_read_workflow_types() == (
        "MoonMind.UserWorkflow",
        "MoonMind.ManifestIngest",
    )
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.ManifestIngest")
    require_product_projection("MoonMind.ManifestIngest", include_historical=True)
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.NotAType", include_historical=True)


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
