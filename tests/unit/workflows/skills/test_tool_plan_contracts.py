from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_plan_contracts import (
    ContractValidationError,
)

def test_step_skills_accepts_valid_properties() -> None:
    """T002: Ensure step.skills structures successfully marshal."""
    raw_payload = {
        "id": "node-1",
        "tool": {
            "name": "foo",
            "type": "skill",
        },
        "inputs": {},
        "skills": {
            "sets": ["default", "node-specific"],
            "include": [
                {"name": "test-skill-1"},
                {"name": "test-skill-2"},
            ],
            "exclude": ["legacy"],
            "materializationMode": "hybrid",
        },
    }
    
    # We should test parse_step or Step directly, depending on how skills is injected
    # Since Step is a frozen dataclass, we initialize it directly with the payload attrs
    from moonmind.workflows.skills.tool_plan_contracts import parse_step
    
    step = parse_step(raw_payload)
    
    assert step.skills is not None
    assert step.skills.sets == ("default", "node-specific")
    assert len(step.skills.include) == 2
    assert step.skills.include[0].name == "test-skill-1"
    assert step.skills.include[1].name == "test-skill-2"
    assert step.skills.exclude == ("legacy",)
    assert step.skills.materialization_mode == "hybrid"

def test_step_skills_rejects_invalid_values() -> None:
    """T002: Assert structure validation handles edge cases for skills."""
    from moonmind.workflows.skills.tool_plan_contracts import parse_step

    with pytest.raises(ContractValidationError, match="node.skills.sets must be a list"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"sets": "not-a-list"},
        })

    with pytest.raises(ContractValidationError, match="node.skills.include must be a list"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"include": "not-a-list"},
        })

    with pytest.raises(ContractValidationError, match="node.skills.exclude must be a list"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"exclude": "not-a-list"},
        })

    with pytest.raises(ContractValidationError, match="node.skills.materializationMode must be hybrid, remote, local, or none"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"materializationMode": "invalid"},
        })

    with pytest.raises(ContractValidationError, match="semantic versions"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"include": [{"name": "test-skill", "version": "1.0.0"}]},
        })

    with pytest.raises(ContractValidationError, match="semantic versions"):
        parse_step({
            "id": "node-1", "tool": {"name": "foo"}, "inputs": {},
            "skills": {"include": [{"name": "test-skill:1.0.0"}]},
        })


def test_tool_failure_renders_its_code_and_message() -> None:
    """An uncaught ToolFailure must carry its diagnosis into the traceback."""
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    failure = ToolFailure(
        error_code="DEPLOYMENT_RELEASE_FAILED",
        message="Wildcard API bindings require an existing operator URL",
        retryable=False,
        details={"releaseJob": "abc123"},
    )
    rendered = str(failure)
    assert "DEPLOYMENT_RELEASE_FAILED" in rendered
    assert "Wildcard API bindings require an existing operator URL" in rendered


def test_tool_failure_rendering_includes_its_cause() -> None:
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    failure = ToolFailure(
        error_code="OUTER",
        message="outer failed",
        retryable=False,
        cause=ToolFailure(error_code="INNER", message="inner failed", retryable=True),
    )
    rendered = str(failure)
    assert "outer failed" in rendered
    assert "inner failed" in rendered
