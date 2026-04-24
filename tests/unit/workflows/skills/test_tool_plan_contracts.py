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
            "version": "1.0.0"
        },
        "inputs": {},
        "skills": {
            "sets": ["default", "node-specific"],
            "include": [
                {"name": "test-skill-1", "version": "1.0.0"},
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
    assert step.skills.include[0].version == "1.0.0"
    assert step.skills.include[1].name == "test-skill-2"
    assert step.skills.include[1].version is None
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
